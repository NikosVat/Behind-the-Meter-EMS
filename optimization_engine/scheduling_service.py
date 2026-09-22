"""Generic SME Equipment Scheduling & Multi-Resolution MILP Optimization Service.

Solves the multi-resolution constrained equipment scheduling problem:
- Arbitrary time resolutions: 5, 15, 30, and 60 minutes (288, 96, 48, 24 slots).
- Strict non-interruptible contiguity or flexible interruptible slot distribution.
- Hard must-run constraints vs. soft optional asset omission.
- Permissible time windows including midnight-crossing ranges (e.g. 22:00 to 06:00).
- Facility peak capacity capping with soft penalty slack.
- Soft preferred start times with priority-weighted deviation penalties.
- Objective modes: 'cost' (minimize energy bill), 'peak' (minimize peak load), or 'balanced'.
- Conservative baseline uncertainty margin preventing real-world replay infeasibility.
- Advisory-only explanation generator producing human-interpretable Greek action summaries.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from optimization_engine.scheduling_models import (
    GeneratedSchedule,
    GenericEquipmentAsset,
    ScheduleItem,
    ScheduleSettings,
    TimelineLoad,
)


class EquipmentSchedulingService:
    """Multi-resolution MILP solver and advisory schedule generator for commercial facilities."""

    def __init__(
        self,
        settings: ScheduleSettings,
        assets: list[GenericEquipmentAsset],
        schedule_date: str | None = None,
        baseline_load_kw: list[float] | None = None,
        tariff_rates_eur_kwh: list[float] | None = None,
        input_source: str = "forecast",
        is_demo: bool = False,
    ):
        self.settings = settings
        self.assets = [a for a in assets if a.enabled]
        self.schedule_date = schedule_date or datetime.now(timezone.utc).date().isoformat()
        self.time_step_minutes = settings.time_step_minutes
        self.dt = self.time_step_minutes / 60.0  # slot duration in hours
        self.num_slots = int(1440 / self.time_step_minutes)
        self.input_source = input_source
        self.is_demo = is_demo

        # Normalize baseline and tariffs to num_slots
        self.raw_baseline = baseline_load_kw
        self.raw_tariffs = tariff_rates_eur_kwh
        self.baseline_kw = self._resample_or_default_baseline(baseline_load_kw)
        self.tariff_eur_kwh = self._resample_or_default_tariffs(tariff_rates_eur_kwh)

        # Apply conservative forecast uncertainty margin
        uncertainty_pct = max(0.0, float(settings.forecast_uncertainty_pct))
        self.uncertainty_factor = 1.0 + (uncertainty_pct / 100.0)
        self.conservative_baseline_kw = [b * self.uncertainty_factor for b in self.baseline_kw]
        self.uncertainty_margin_kw = float(np.mean(self.conservative_baseline_kw) - np.mean(self.baseline_kw))

    # -----------------------------------------------------------------
    # Time & Window Parsing
    # -----------------------------------------------------------------

    def time_to_slot(self, time_str: str) -> int:
        """Convert HH:MM to slot index in [0, num_slots - 1]."""
        parts = time_str.strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        total_mins = h * 60 + m
        slot = total_mins // self.time_step_minutes
        return min(slot, self.num_slots - 1)

    def slot_to_time(self, slot: int) -> str:
        """Convert slot index to HH:MM string."""
        total_mins = (slot % self.num_slots) * self.time_step_minutes
        h = (total_mins // 60) % 24
        m = total_mins % 60
        return f"{h:02d}:{m:02d}"

    def get_allowed_slots(self, earliest: str, latest: str) -> list[int]:
        """Compute allowed slots for an asset, supporting midnight-crossing windows."""
        s_start = self.time_to_slot(earliest)
        s_finish = self.time_to_slot(latest)

        if s_start <= s_finish:
            return list(range(s_start, s_finish + 1))
        else:
            # Crosses midnight (e.g. 22:00 to 06:00)
            return list(range(s_start, self.num_slots)) + list(range(s_finish + 1))

    def get_valid_start_slots(self, asset: GenericEquipmentAsset, k_slots: int) -> list[int]:
        """Compute valid start slots where asset can run contiguously within allowed window."""
        allowed_set = set(self.get_allowed_slots(asset.earliest_start, asset.latest_finish))
        valid_starts = []
        for s in allowed_set:
            # Check if all k_slots contiguous slots are within allowed_set
            all_valid = True
            for tau in range(k_slots):
                slot = (s + tau) % self.num_slots
                if slot not in allowed_set:
                    all_valid = False
                    break
            if all_valid:
                valid_starts.append(s)
        return valid_starts

    # -----------------------------------------------------------------
    # Baseline & Tariff Normalization
    # -----------------------------------------------------------------

    def _resample_or_default_baseline(self, raw: list[float] | None) -> list[float]:
        """Normalize baseline power array to self.num_slots."""
        if raw is not None and len(raw) == self.num_slots:
            return [max(0.0, float(x)) for x in raw]
        elif raw is not None and len(raw) == 24:
            # Expand 24 hourly values to num_slots
            ratio = self.num_slots // 24
            expanded = []
            for val in raw:
                expanded.extend([max(0.0, float(val))] * ratio)
            return expanded[:self.num_slots]

        # Default Greek commercial baseline load curve
        # Night: 4 kW, Morning preheat: 12 kW, Daytime: 8 kW, Afternoon peak: 14 kW
        hourly_defaults = [
            4.0, 4.0, 4.0, 4.5, 6.0, 10.0, 12.0, 10.0,
            8.0, 8.0, 9.0, 9.0, 8.5, 12.0, 14.0, 11.0,
            8.0, 7.0, 6.0, 5.5, 5.0, 4.5, 4.0, 4.0
        ]
        ratio = self.num_slots // 24
        expanded = []
        for val in hourly_defaults:
            expanded.extend([val] * ratio)
        return expanded[:self.num_slots]

    def _resample_or_default_tariffs(self, raw: list[float] | None) -> list[float]:
        """Normalize tariff rate array to self.num_slots."""
        if raw is not None and len(raw) == self.num_slots:
            return [max(0.01, float(x)) for x in raw]
        elif raw is not None and len(raw) == 24:
            ratio = self.num_slots // 24
            expanded = []
            for val in raw:
                expanded.extend([max(0.01, float(val))] * ratio)
            return expanded[:self.num_slots]

        # Default Greek Commercial Tariff Γ22 (Off-peak: 0.12, Peak: 0.32, Normal: 0.20)
        hourly_rates = [0.20] * 24
        for h in range(1, 5):
            hourly_rates[h] = 0.12  # Night off-peak
        for h in range(13, 17):
            hourly_rates[h] = 0.32  # Summer afternoon peak
        ratio = self.num_slots // 24
        expanded = []
        for val in hourly_rates:
            expanded.extend([val] * ratio)
        return expanded[:self.num_slots]

    # -----------------------------------------------------------------
    # Optimization Engine (SciPy HiGHS MILP)
    # -----------------------------------------------------------------

    def solve(self) -> GeneratedSchedule:
        """Formulate and solve the generic equipment scheduling problem."""
        assumptions: list[str] = [
            "Συμβουλευτικός προγραμματισμός (Advisory-only). Δεν ασκείται αυτόματος φυσικός έλεγχος εξοπλισμού.",
            f"Χρονική ανάλυση προγραμματισμού: {self.time_step_minutes} λεπτά ({self.num_slots} χρονικές θυρίδες ανά 24ωρο).",
            f"Εφαρμόστηκε συντηρητικό περιθώριο αβεβαιότητας πρόγνωσης {self.settings.forecast_uncertainty_pct:.1f}% (+{self.uncertainty_margin_kw:.2f} kW).",
        ]
        warnings: list[str] = []

        if self.is_demo:
            warnings.append("Προσοχή: Χρησιμοποιήθηκαν ενδεικτικά δεδομένα επίδειξης. Τα αποτελέσματα είναι εκτιμήσεις και όχι επαληθευμένη εξοικονόμηση.")

        # If no assets enabled, return simple baseline schedule
        if not self.assets:
            return self._build_empty_schedule(assumptions, warnings)

        # 1. Filter assets active on the schedule date's weekday
        target_date = date.fromisoformat(self.schedule_date)
        weekday = target_date.weekday()  # 0=Monday, 6=Sunday
        active_assets = [a for a in self.assets if weekday in a.active_weekdays]

        if not active_assets:
            warnings.append(f"Κανένας εξοπλισμός δεν είναι δηλωμένος ενεργός για την ημέρα ({target_date.strftime('%A')}).")
            return self._build_empty_schedule(assumptions, warnings)

        # 2. Check for impossible hard windows
        filtered_assets: list[GenericEquipmentAsset] = []
        asset_slots_needed: dict[str, int] = {}
        valid_start_slots_map: dict[str, list[int]] = {}
        allowed_slots_map: dict[str, list[int]] = {}

        for asset in active_assets:
            k = max(1, math.ceil(asset.required_runtime_minutes / self.time_step_minutes))
            asset_slots_needed[asset.asset_id] = k

            if not asset.interruptible:
                valid_starts = self.get_valid_start_slots(asset, k)
                if not valid_starts:
                    if asset.must_run:
                        raise ValueError(
                            f"Μη εφικτό παράθυρο για '{asset.name}': Απαιτείται συνεχής λειτουργία {asset.required_runtime_minutes} λεπτών, "
                            f"αλλά το επιτρεπόμενο παράθυρο ({asset.earliest_start} - {asset.latest_finish}) δεν επαρκεί."
                        )
                    else:
                        warnings.append(f"Η προαιρετική συσκευή '{asset.name}' εξαιρέθηκε διότι το παράθυρο λειτουργίας δεν επαρκεί.")
                        continue
                valid_start_slots_map[asset.asset_id] = valid_starts
            else:
                allowed = self.get_allowed_slots(asset.earliest_start, asset.latest_finish)
                if len(allowed) < k:
                    if asset.must_run:
                        raise ValueError(
                            f"Μη εφικτό παράθυρο για διακοπτόμενη συσκευή '{asset.name}': Απαιτούνται {k} θυρίδες, αλλά υπάρχουν μόνο {len(allowed)} διαθέσιμες."
                        )
                    else:
                        warnings.append(f"Η προαιρετική συσκευή '{asset.name}' εξαιρέθηκε διότι οι διαθέσιμες θυρίδες δεν επαρκούν.")
                        continue
                allowed_slots_map[asset.asset_id] = allowed

            filtered_assets.append(asset)

        # 3. Variable Mapping
        # Variables:
        # - For non-interruptible asset i: binary start variables u_{i, s} for s in valid_start_slots
        # - For interruptible asset j: binary run variables x_{j, t} for t in allowed_slots
        # - Optional asset omission indicator z_i in {0, 1}
        # - Slot total power P_total_t >= 0
        # - Capacity slack S_t >= 0
        # - Peak power P_peak >= 0
        var_map: dict[str, int] = {}
        integrality: list[int] = []
        lower_bounds: list[float] = []
        upper_bounds: list[float] = []
        c_obj: list[float] = []

        def add_var(name: str, is_int: bool, lb: float, ub: float, cost: float) -> int:
            idx = len(var_map)
            var_map[name] = idx
            integrality.append(1 if is_int else 0)
            lower_bounds.append(lb)
            upper_bounds.append(ub)
            c_obj.append(cost)
            return idx

        # Asset variables
        for asset in filtered_assets:
            aid = asset.asset_id
            k = asset_slots_needed[aid]
            pref_slot = self.time_to_slot(asset.preferred_start) if asset.preferred_start else None

            if not asset.interruptible:
                for s in valid_start_slots_map[aid]:
                    # Soft preference penalty
                    pref_cost = 0.0
                    if pref_slot is not None:
                        dist = min(abs(s - pref_slot), self.num_slots - abs(s - pref_slot))
                        pref_cost = dist * 0.02 * asset.priority

                    add_var(f"u_{aid}_{s}", is_int=True, lb=0.0, ub=1.0, cost=pref_cost)
            else:
                for t in allowed_slots_map[aid]:
                    pref_cost = 0.0
                    if pref_slot is not None:
                        dist = min(abs(t - pref_slot), self.num_slots - abs(t - pref_slot))
                        pref_cost = dist * 0.01 * asset.priority

                    add_var(f"x_{aid}_{t}", is_int=True, lb=0.0, ub=1.0, cost=pref_cost)

            if not asset.must_run:
                # Omission penalty: high priority optional assets are penalized if omitted
                omission_penalty = (6.0 - asset.priority) * 5.0
                add_var(f"omitted_{aid}", is_int=True, lb=0.0, ub=1.0, cost=omission_penalty)

        # Slot total power and capacity slacks
        capacity_limit = float(self.settings.max_facility_power_kw)
        obj_mode = self.settings.objective_mode.lower()

        for t in range(self.num_slots):
            # Energy cost weight
            energy_rate = self.tariff_eur_kwh[t] * self.dt
            if obj_mode == "cost":
                p_cost = energy_rate
            elif obj_mode == "peak":
                p_cost = 0.005 * energy_rate
            else:  # balanced
                p_cost = energy_rate

            add_var(f"ptot_{t}", is_int=False, lb=0.0, ub=1000.0, cost=p_cost)
            # Capacity breach penalty slack
            add_var(f"slack_{t}", is_int=False, lb=0.0, ub=1000.0, cost=200.0)

        # Peak power variable
        peak_weight = 10.0 if obj_mode == "peak" else (2.5 if obj_mode == "balanced" else 0.01)
        add_var("peak_p", is_int=False, lb=0.0, ub=1000.0, cost=peak_weight)

        num_vars = len(var_map)

        # 4. Constraints Construction
        A_rows: list[list[float]] = []
        lhs_bounds: list[float] = []
        rhs_bounds: list[float] = []

        def add_row(row_dict: dict[int, float], lhs: float, rhs: float):
            row = [0.0] * num_vars
            for idx, val in row_dict.items():
                row[idx] = val
            A_rows.append(row)
            lhs_bounds.append(lhs)
            rhs_bounds.append(rhs)

        # Constraint A: Asset execution count
        for asset in filtered_assets:
            aid = asset.asset_id
            k = asset_slots_needed[aid]
            row_dict: dict[int, float] = {}

            if not asset.interruptible:
                for s in valid_start_slots_map[aid]:
                    row_dict[var_map[f"u_{aid}_{s}"]] = 1.0
                if not asset.must_run:
                    row_dict[var_map[f"omitted_{aid}"]] = 1.0
                    add_row(row_dict, lhs=1.0, rhs=1.0)
                else:
                    add_row(row_dict, lhs=1.0, rhs=1.0)
            else:
                for t in allowed_slots_map[aid]:
                    row_dict[var_map[f"x_{aid}_{t}"]] = 1.0
                if not asset.must_run:
                    row_dict[var_map[f"omitted_{aid}"]] = float(k)
                    add_row(row_dict, lhs=float(k), rhs=float(k))
                else:
                    add_row(row_dict, lhs=float(k), rhs=float(k))

        # Constraint B: Power Balance for each slot t
        # P_total_t - sum_i P_i * x_{i,t} = P_conservative_baseline_t
        for t in range(self.num_slots):
            row_dict = {var_map[f"ptot_{t}"]: 1.0}

            for asset in filtered_assets:
                aid = asset.asset_id
                k = asset_slots_needed[aid]
                p_kw = asset.rated_power_kw

                if not asset.interruptible:
                    # Find all valid starts s that cover slot t
                    for s in valid_start_slots_map[aid]:
                        covered = False
                        for tau in range(k):
                            if (s + tau) % self.num_slots == t:
                                covered = True
                                break
                        if covered:
                            u_idx = var_map[f"u_{aid}_{s}"]
                            row_dict[u_idx] = row_dict.get(u_idx, 0.0) - p_kw
                else:
                    if t in allowed_slots_map[aid]:
                        x_idx = var_map[f"x_{aid}_{t}"]
                        row_dict[x_idx] = row_dict.get(x_idx, 0.0) - p_kw

            base_val = self.conservative_baseline_kw[t]
            add_row(row_dict, lhs=base_val, rhs=base_val)

        # Constraint C: Contracted Capacity Limit
        # P_total_t - S_t <= P_max
        for t in range(self.num_slots):
            row_dict = {
                var_map[f"ptot_{t}"]: 1.0,
                var_map[f"slack_{t}"]: -1.0,
            }
            add_row(row_dict, lhs=-np.inf, rhs=capacity_limit)

        # Constraint D: Peak power tracking
        # P_peak - P_total_t >= 0
        for t in range(self.num_slots):
            row_dict = {
                var_map["peak_p"]: 1.0,
                var_map[f"ptot_{t}"]: -1.0,
            }
            add_row(row_dict, lhs=0.0, rhs=np.inf)

        # 5. Solve MILP
        A_matrix = np.array(A_rows, dtype=float)
        constraints = LinearConstraint(A_matrix, lhs_bounds, rhs_bounds)
        bounds = Bounds(lower_bounds, upper_bounds)

        res = milp(c=np.array(c_obj), integrality=integrality, bounds=bounds, constraints=constraints)

        if not res.success:
            raise ValueError(f"Ο επιλύτης δεν μπόρεσε να βρει εφικτό πρόγραμμα: {res.status} ({res.message})")

        sol = res.x

        # 6. Extract Solution & Schedule Items
        schedule_items: list[ScheduleItem] = []
        optimized_equipment_power = np.zeros(self.num_slots)

        for asset in filtered_assets:
            aid = asset.asset_id
            k = asset_slots_needed[aid]
            pref_slot = self.time_to_slot(asset.preferred_start) if asset.preferred_start else None

            is_omitted = False
            if not asset.must_run:
                is_omitted = (sol[var_map[f"omitted_{aid}"]] > 0.5)

            if is_omitted:
                schedule_items.append(
                    ScheduleItem(
                        asset_id=aid,
                        asset_name=asset.name,
                        category=asset.category,
                        rated_power_kw=asset.rated_power_kw,
                        start_time=None,
                        end_time=None,
                        duration_minutes=asset.required_runtime_minutes,
                        scheduled_slots=[],
                        scheduled_power_kw=0.0,
                        explanation=f"Η προαιρετική συσκευή '{asset.name}' δεν προγραμματίστηκε για να αποτραπεί υπέρβαση της μέγιστης ισχύος ({capacity_limit} kW).",
                        preference_satisfied=False,
                        is_omitted=True,
                    )
                )
                warnings.append(f"Η συσκευή '{asset.name}' εξαιρέθηκε από τον προγραμματισμό για λόγους διαθεσιμότητας ισχύος.")
                continue

            scheduled_slots: list[int] = []
            if not asset.interruptible:
                start_slot = None
                for s in valid_start_slots_map[aid]:
                    if sol[var_map[f"u_{aid}_{s}"]] > 0.5:
                        start_slot = s
                        break
                if start_slot is None:
                    start_slot = valid_start_slots_map[aid][0]

                for tau in range(k):
                    scheduled_slots.append((start_slot + tau) % self.num_slots)
            else:
                for t in allowed_slots_map[aid]:
                    if sol[var_map[f"x_{aid}_{t}"]] > 0.5:
                        scheduled_slots.append(t)

            for sl in scheduled_slots:
                optimized_equipment_power[sl] += asset.rated_power_kw

            # Compute timing & human explanation
            scheduled_slots.sort()
            first_slot = scheduled_slots[0] if scheduled_slots else 0
            last_slot = scheduled_slots[-1] if scheduled_slots else 0
            start_str = self.slot_to_time(first_slot)
            end_str = self.slot_to_time((last_slot + 1) % self.num_slots)

            pref_satisfied = True
            if pref_slot is not None:
                pref_satisfied = (first_slot == pref_slot)

            explanation = self._generate_asset_explanation(asset, scheduled_slots, pref_slot, pref_satisfied)

            schedule_items.append(
                ScheduleItem(
                    asset_id=aid,
                    asset_name=asset.name,
                    category=asset.category,
                    rated_power_kw=asset.rated_power_kw,
                    start_time=start_str,
                    end_time=end_str,
                    duration_minutes=asset.required_runtime_minutes,
                    scheduled_slots=scheduled_slots,
                    scheduled_power_kw=asset.rated_power_kw,
                    explanation=explanation,
                    preference_satisfied=pref_satisfied,
                    is_omitted=False,
                )
            )

        # 7. Compute Baseline Comparison Load & Metrics
        baseline_total_load = self._compute_unmanaged_baseline(active_assets)
        optimized_total_load = np.array(self.baseline_kw) + optimized_equipment_power

        # Energy costs
        baseline_cost = float(np.sum(baseline_total_load * np.array(self.tariff_eur_kwh) * self.dt))
        optimized_cost = float(np.sum(optimized_total_load * np.array(self.tariff_eur_kwh) * self.dt))
        savings_eur = max(0.0, baseline_cost - optimized_cost)

        baseline_peak = float(np.max(baseline_total_load))
        optimized_peak = float(np.max(optimized_total_load))

        if optimized_peak > capacity_limit:
            warnings.append(
                f"Προσοχή: Το βελτιστοποιημένο φορτίο αγγίζει τα {optimized_peak:.1f} kW, "
                f"υπερβαίνοντας το δηλωμένο όριο συμβολαίου των {capacity_limit:.1f} kW."
            )

        # Timestamps for timeline
        timeline_timestamps = [self.slot_to_time(t) for t in range(self.num_slots)]

        timeline_load = TimelineLoad(
            timestamps=timeline_timestamps,
            baseline_kw=[round(float(x), 2) for x in baseline_total_load],
            conservative_baseline_kw=[round(float(x), 2) for x in self.conservative_baseline_kw],
            optimized_kw=[round(float(x), 2) for x in optimized_total_load],
            max_power_limit_kw=capacity_limit,
        )

        return GeneratedSchedule(
            facility_id=self.settings.facility_id,
            schedule_date=self.schedule_date,
            status="preview",
            time_step_minutes=self.time_step_minutes,
            objective_mode=self.settings.objective_mode,
            input_source=self.input_source,
            is_demo=self.is_demo,
            baseline_cost_eur=round(baseline_cost, 2),
            optimized_cost_eur=round(optimized_cost, 2),
            estimated_savings_eur=round(savings_eur, 2),
            baseline_peak_kw=round(baseline_peak, 2),
            optimized_peak_kw=round(optimized_peak, 2),
            uncertainty_margin_kw=round(self.uncertainty_margin_kw, 2),
            assumptions=assumptions,
            warnings=warnings,
            items=schedule_items,
            timeline_load=timeline_load,
        )

    # -----------------------------------------------------------------
    # Baseline Counterfactual Simulation
    # -----------------------------------------------------------------

    def _compute_unmanaged_baseline(self, assets: list[GenericEquipmentAsset]) -> np.ndarray:
        """Simulate unmanaged nominal behavior where each asset runs at earliest/preferred start."""
        unmanaged_load = np.array(self.baseline_kw).copy()

        for asset in assets:
            k = max(1, math.ceil(asset.required_runtime_minutes / self.time_step_minutes))
            nom_time = asset.preferred_start or asset.earliest_start
            start_slot = self.time_to_slot(nom_time)

            for tau in range(k):
                slot = (start_slot + tau) % self.num_slots
                unmanaged_load[slot] += asset.rated_power_kw

        return unmanaged_load

    def _generate_asset_explanation(
        self,
        asset: GenericEquipmentAsset,
        slots: list[int],
        pref_slot: int | None,
        pref_satisfied: bool,
    ) -> str:
        """Generate human-readable Greek operational explanation."""
        start_time = self.slot_to_time(slots[0])
        end_time = self.slot_to_time((slots[-1] + 1) % self.num_slots)

        if asset.interruptible and len(slots) > 1 and (slots[-1] - slots[0] + 1 != len(slots)):
            return f"Η συσκευή '{asset.name}' κατανεμήθηκε σε {len(slots)} διακοπτόμενες θυρίδες ({start_time} - {end_time}) για εξομάλυνση του φορτίου αιχμής."

        if pref_slot is not None:
            pref_time = self.slot_to_time(pref_slot)
            if pref_satisfied:
                return f"Η συσκευή '{asset.name}' προγραμματίστηκε στην προτιμώμενη ώρα έναρξης {pref_time} (τερματισμός: {end_time})."
            else:
                return (
                    f"Η συσκευή '{asset.name}' μεταφέρθηκε από την προτιμώμενη ώρα {pref_time} "
                    f"στις {start_time} (έως {end_time}) για αποφυγή υψηλής χρέωσης τιμολογίου ή υπέρβασης ισχύος."
                )

        if asset.priority >= 4:
            return f"Η συσκευή '{asset.name}' προγραμματίστηκε κατά προτεραιότητα στις {start_time} - {end_time} λόγω υψηλής επιχειρησιακής σημασίας (Προτεραιότητα {asset.priority})."

        return f"Η συσκευή '{asset.name}' τοποθετήθηκε στο χρονικό παράθυρο {start_time} - {end_time} με το χαμηλότερο συνολικό κόστος ρεύματος."

    def _build_empty_schedule(self, assumptions: list[str], warnings: list[str]) -> GeneratedSchedule:
        """Return an empty schedule when no assets are scheduled."""
        baseline_load = np.array(self.baseline_kw)
        cost = float(np.sum(baseline_load * np.array(self.tariff_eur_kwh) * self.dt))
        peak = float(np.max(baseline_load))
        timestamps = [self.slot_to_time(t) for t in range(self.num_slots)]

        return GeneratedSchedule(
            facility_id=self.settings.facility_id,
            schedule_date=self.schedule_date,
            status="preview",
            time_step_minutes=self.time_step_minutes,
            objective_mode=self.settings.objective_mode,
            input_source=self.input_source,
            is_demo=self.is_demo,
            baseline_cost_eur=round(cost, 2),
            optimized_cost_eur=round(cost, 2),
            estimated_savings_eur=0.0,
            baseline_peak_kw=round(peak, 2),
            optimized_peak_kw=round(peak, 2),
            uncertainty_margin_kw=round(self.uncertainty_margin_kw, 2),
            assumptions=assumptions,
            warnings=warnings,
            items=[],
            timeline_load=TimelineLoad(
                timestamps=timestamps,
                baseline_kw=[round(float(x), 2) for x in baseline_load],
                conservative_baseline_kw=[round(float(x), 2) for x in self.conservative_baseline_kw],
                optimized_kw=[round(float(x), 2) for x in baseline_load],
                max_power_limit_kw=float(self.settings.max_facility_power_kw),
            ),
        )


def _parse_time_to_slot(time_str: str, time_step_minutes: int) -> int:
    """Parse HH:MM string to slot index for given resolution."""
    parts = time_str.strip().split(":")
    h, m = int(parts[0]), int(parts[1])
    total_mins = h * 60 + m
    return total_mins // time_step_minutes


def _format_slot_to_time(slot: int, time_step_minutes: int) -> str:
    """Format slot index to HH:MM string for given resolution."""
    total_mins = slot * time_step_minutes
    if total_mins >= 1440:
        return "24:00"
    h = (total_mins // 60) % 24
    m = total_mins % 60
    return f"{h:02d}:{m:02d}"


def schedule_sme_equipment(
    facility_id: str,
    assets: list[GenericEquipmentAsset],
    settings: ScheduleSettings,
    schedule_date: str | date | None = None,
    baseline_load: list[float] | None = None,
    tariff_rates: list[float] | None = None,
    input_source: str = "forecast",
    is_demo: bool = False,
) -> GeneratedSchedule:
    """Functional convenience wrapper around EquipmentSchedulingService."""
    date_str = schedule_date.isoformat() if isinstance(schedule_date, date) else schedule_date

    # Pre-check: if a must-run asset exceeds max_facility_power_kw, mark infeasible gracefully
    for a in assets:
        if a.must_run and a.rated_power_kw > settings.max_facility_power_kw:
            svc = EquipmentSchedulingService(
                settings=settings,
                assets=assets,
                schedule_date=date_str,
                baseline_load_kw=baseline_load,
                tariff_rates_eur_kwh=tariff_rates,
                input_source=input_source,
                is_demo=is_demo,
            )
            empty = svc._build_empty_schedule(
                assumptions=["Η συσκευή υπερβαίνει τη συμφωνημένη ισχύ της εγκατάστασης."],
                warnings=[
                    f"Η συσκευή '{a.name}' απαιτεί {a.rated_power_kw} kW αλλά η μέγιστη ισχύς είναι {settings.max_facility_power_kw} kW."
                ],
            )
            empty.status = "infeasible"
            return empty

    service = EquipmentSchedulingService(
        settings=settings,
        assets=assets,
        schedule_date=date_str,
        baseline_load_kw=baseline_load,
        tariff_rates_eur_kwh=tariff_rates,
        input_source=input_source,
        is_demo=is_demo,
    )
    try:
        schedule = service.solve()
        schedule.status = "optimal"
        return schedule
    except ValueError as ex:
        empty = service._build_empty_schedule(
            assumptions=[],
            warnings=[str(ex)],
        )
        empty.status = "infeasible"
        return empty

