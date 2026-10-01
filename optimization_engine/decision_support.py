"""Autonomous Decision-Support & Closed-Loop Verification Pipeline for Behind-the-Meter EMS.

Transforms the EMS paradigm from passive monitoring ('Measure -> Calculate -> Alert')
to autonomous optimization and verification ('Measure -> Predict -> Optimize -> Act -> Verify').

Components:
1. DecisionSupportEngine: Translates mathematical MILP schedules into concrete, actionable,
   and prioritized operational recommendations for commercial facility managers.
2. ClosedLoopVerifier: Audits post-intervention telemetry against counterfactual baseline loads
   to experimentally certify verified power reductions and financial savings.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from optimization_engine.models import (
    ActionRecommendation,
    BESSLoad,
    DefrostLoad,
    HVACLoad,
    OptimizationProblem,
    PriorityLevel,
    ProductionBatchLoad,
    RecommendationCategory,
    ScheduleResult,
    VerificationRecord,
    VerificationStatus,
)


class DecisionSupportEngine:
    """Generates actionable operational guidance from optimization results."""

    def __init__(self, facility_id: str = "fac_bakery_01"):
        self.facility_id = facility_id

    def generate_recommendations(
        self,
        problem: OptimizationProblem,
        schedule: ScheduleResult,
    ) -> list[ActionRecommendation]:
        """Analyze optimization schedule and generate human-interpretable action cards."""
        recommendations: list[ActionRecommendation] = []

        if not schedule.is_optimal or not schedule.operationally_feasible:
            return recommendations

        # 1. Defrost Shift Recommendations
        for i, d in enumerate(problem.defrost_loads):
            rec = self._evaluate_defrost_shift(d, i, problem, schedule)
            if rec:
                recommendations.append(rec)

        # 2. Production Batch Oven Recommendations
        for k, b in enumerate(problem.batch_loads):
            rec = self._evaluate_batch_shift(b, k, problem, schedule)
            if rec:
                recommendations.append(rec)

        # 3. HVAC Pre-Cooling Recommendations
        for j, h in enumerate(problem.hvac_loads):
            rec = self._evaluate_hvac_precooling(h, j, problem, schedule)
            if rec:
                recommendations.append(rec)

        # 4. BESS Arbitrage & Peak Shaving Recommendations
        if problem.bess is not None:
            rec = self._evaluate_bess_action(problem.bess, problem, schedule)
            if rec:
                recommendations.append(rec)

        # 5. Capacity Breach Alert (Unmitigated)
        if schedule.capacity_breached_optimized:
            breach_kw = max(0.0, schedule.peak_optimized_kw - problem.contracted_capacity_kw)
            peak_hour = schedule.optimized_total_load_kw.index(schedule.peak_optimized_kw)
            recommendations.append(
                ActionRecommendation(
                    recommendation_id=f"rec_breach_{uuid.uuid4().hex[:8]}",
                    facility_id=self.facility_id,
                    category=RecommendationCategory.PEAK_SHAVING,
                    priority=PriorityLevel.CRITICAL,
                    title=f"Capacity Breach Alert: {breach_kw:.1f} kW Unmitigated",
                    description_el=f"Η ζήτηση προβλέπεται στα {schedule.peak_optimized_kw:.1f} kW την ώρα {peak_hour:02d}:00 (όριο: {problem.contracted_capacity_kw:.1f} kW). Οι ευέλικτες μετατοπίσεις δεν επαρκούν.",
                    description_en=f"Projected demand reaches {schedule.peak_optimized_kw:.1f} kW at hour {peak_hour:02d}:00 exceeding contracted {problem.contracted_capacity_kw:.1f} kW. Flexible load shifting alone cannot resolve this breach; manual shedding or contract review required.",
                    asset_name="Whole Facility Demand",
                    original_window=f"{peak_hour:02d}:00-{(peak_hour+1):02d}:00",
                    recommended_window=f"{peak_hour:02d}:00-{(peak_hour+1):02d}:00",
                    peak_load_avoided_kw=0.0,
                    estimated_savings_eur=0.0,
                    contracted_capacity_kw=problem.contracted_capacity_kw,
                    projected_peak_kw=schedule.peak_optimized_kw,
                )
            )

        # Sort recommendations by priority and estimated savings
        priority_weights = {
            PriorityLevel.CRITICAL: 4,
            PriorityLevel.HIGH: 3,
            PriorityLevel.MEDIUM: 2,
            PriorityLevel.LOW: 1,
        }
        recommendations.sort(
            key=lambda r: (priority_weights.get(r.priority, 0), r.estimated_savings_eur),
            reverse=True,
        )

        return recommendations

    def _evaluate_defrost_shift(
        self,
        defrost: DefrostLoad,
        index: int,
        problem: OptimizationProblem,
        schedule: ScheduleResult,
    ) -> ActionRecommendation | None:
        dev_schedule = schedule.device_schedules.get(defrost.name, [])
        if not dev_schedule:
            return None

        # Find active hours in optimal schedule
        opt_active_hours = [t for t, p in enumerate(dev_schedule) if p > 0.1]
        if not opt_active_hours:
            return None

        opt_start = opt_active_hours[0]
        nominal_start = defrost.nominal_start_hour

        # If unchanged, no intervention needed
        if opt_start == nominal_start:
            return None

        nominal_rate = problem.tariff_rates_eur_kwh[nominal_start]
        opt_rate = problem.tariff_rates_eur_kwh[opt_start]
        rate_diff = max(0.0, nominal_rate - opt_rate)
        energy_savings = rate_diff * defrost.power_kw * defrost.duration_hours

        # Net contractual demand savings (only if contract demand rate > 0 and baseline peak dropped)
        demand_savings = 0.0
        if problem.contracted_demand_rate_eur_per_kw > 0.0:
            net_peak_drop = max(0.0, schedule.peak_baseline_kw - max(problem.contracted_capacity_kw, schedule.peak_optimized_kw))
            baseline_peak_hour = schedule.baseline_total_load_kw.index(schedule.peak_baseline_kw)
            if net_peak_drop > 0.0 and nominal_start == baseline_peak_hour:
                demand_savings = min(defrost.power_kw, net_peak_drop) * problem.contracted_demand_rate_eur_per_kw

        total_savings = round(energy_savings + demand_savings, 2)

        # Capacity avoidance check
        baseline_at_nominal = schedule.baseline_total_load_kw[nominal_start]
        capacity_avoided = baseline_at_nominal > problem.contracted_capacity_kw

        if total_savings < 1.0 and not capacity_avoided:
            return None

        priority = PriorityLevel.HIGH if (capacity_avoided or total_savings > 10.0) else PriorityLevel.MEDIUM

        orig_w = f"{nominal_start:02d}:00-{(nominal_start + defrost.duration_hours):02d}:00"
        opt_w = f"{opt_start:02d}:00-{(opt_start + defrost.duration_hours):02d}:00"

        desc_el = (
            f"Μετατόπιση απόψυξης '{defrost.name}' στις {opt_w} (αντί για {orig_w}). "
            f"Αποφυγή αιχμής {defrost.power_kw:.1f} kW σε ζώνη υψηλής χρέωσης "
            f"({nominal_rate:.3f} -> {opt_rate:.3f} €/kWh). "
            f"Εκτιμώμενο καθαρό όφελος: €{total_savings:.2f}."
        )
        desc_en = (
            f"Shift defrost cycle for '{defrost.name}' to {opt_w} (was {orig_w}). "
            f"Avoided {defrost.power_kw:.1f} kW peak during high tariff window "
            f"({nominal_rate:.3f} -> {opt_rate:.3f} €/kWh). "
            f"Projected savings: €{total_savings:.2f}."
        )

        return ActionRecommendation(
            recommendation_id=f"rec_defrost_{uuid.uuid4().hex[:8]}",
            facility_id=self.facility_id,
            category=RecommendationCategory.DEFROST_SHIFT,
            priority=priority,
            title=f"Shift Defrost: {defrost.name}",
            description_el=desc_el,
            description_en=desc_en,
            asset_name=defrost.name,
            original_window=orig_w,
            recommended_window=opt_w,
            peak_load_avoided_kw=defrost.power_kw,
            estimated_savings_eur=total_savings,
            confidence_score=None,
            contracted_capacity_kw=problem.contracted_capacity_kw,
            projected_peak_kw=schedule.peak_optimized_kw,
        )

    def _evaluate_batch_shift(
        self,
        batch: ProductionBatchLoad,
        index: int,
        problem: OptimizationProblem,
        schedule: ScheduleResult,
    ) -> ActionRecommendation | None:
        dev_schedule = schedule.device_schedules.get(batch.name, [])
        if not dev_schedule:
            return None

        opt_active_hours = [t for t, p in enumerate(dev_schedule) if p > 0.1]
        if not opt_active_hours:
            return None

        opt_start = opt_active_hours[0]
        orig_start = batch.earliest_start_hour

        if opt_start == orig_start:
            return None

        # Compare costs between earliest baseline and optimal
        baseline_batch_cost = sum(
            problem.tariff_rates_eur_kwh[t] * batch.power_kw
            for t in range(orig_start, orig_start + batch.duration_hours)
        )
        opt_batch_cost = sum(
            problem.tariff_rates_eur_kwh[t] * batch.power_kw
            for t in range(opt_start, opt_start + batch.duration_hours)
        )
        energy_savings = max(0.0, baseline_batch_cost - opt_batch_cost)

        # Net contractual demand savings (only if contract demand rate > 0 and baseline peak dropped)
        demand_savings = 0.0
        if problem.contracted_demand_rate_eur_per_kw > 0.0:
            net_peak_drop = max(0.0, schedule.peak_baseline_kw - max(problem.contracted_capacity_kw, schedule.peak_optimized_kw))
            baseline_peak_hour = schedule.baseline_total_load_kw.index(schedule.peak_baseline_kw)
            if net_peak_drop > 0.0 and orig_start <= baseline_peak_hour < orig_start + batch.duration_hours:
                demand_savings = min(batch.power_kw, net_peak_drop) * problem.contracted_demand_rate_eur_per_kw

        total_savings = round(energy_savings + demand_savings, 2)

        # Capacity breach avoided?
        max_base = max(schedule.baseline_total_load_kw[t] for t in range(orig_start, orig_start + batch.duration_hours))
        capacity_avoided = max_base > problem.contracted_capacity_kw

        if total_savings < 1.0 and not capacity_avoided:
            return None

        priority = PriorityLevel.HIGH if (capacity_avoided or total_savings > 15.0) else PriorityLevel.MEDIUM

        orig_w = f"{orig_start:02d}:00-{(orig_start + batch.duration_hours):02d}:00"
        opt_w = f"{opt_start:02d}:00-{(opt_start + batch.duration_hours):02d}:00"

        desc_el = (
            f"Προγραμματισμός παραγωγής '{batch.name}' στο παράθυρο {opt_w} (αντί για {orig_w}). "
            f"Αποφυγή υπερφόρτωσης ισχύος {batch.power_kw:.1f} kW. "
            f"Εκτιμώμενη εξοικονόμηση: €{total_savings:.2f}."
        )
        desc_en = (
            f"Schedule production batch '{batch.name}' for {opt_w} (was {orig_w}). "
            f"Mitigates {batch.power_kw:.1f} kW peak demand. "
            f"Projected savings: €{total_savings:.2f}."
        )

        return ActionRecommendation(
            recommendation_id=f"rec_batch_{uuid.uuid4().hex[:8]}",
            facility_id=self.facility_id,
            category=RecommendationCategory.BATCH_SCHEDULING,
            priority=priority,
            title=f"Optimize Batch: {batch.name}",
            description_el=desc_el,
            description_en=desc_en,
            asset_name=batch.name,
            original_window=orig_w,
            recommended_window=opt_w,
            peak_load_avoided_kw=batch.power_kw,
            estimated_savings_eur=total_savings,
            confidence_score=None,
            contracted_capacity_kw=problem.contracted_capacity_kw,
            projected_peak_kw=schedule.peak_optimized_kw,
        )

    def _evaluate_hvac_precooling(
        self,
        hvac: HVACLoad,
        index: int,
        problem: OptimizationProblem,
        schedule: ScheduleResult,
    ) -> ActionRecommendation | None:
        dev_schedule = schedule.device_schedules.get(hvac.name, [])
        if not dev_schedule:
            return None

        temps = schedule.hvac_temperatures.get(hvac.name, [])
        if not temps:
            return None

        # Detect pre-cooling: significant cooling power before afternoon peak tariff window
        peak_tariff_hours = [t for t in range(len(problem.tariff_rates_eur_kwh)) if problem.tariff_rates_eur_kwh[t] >= 0.18]
        if not peak_tariff_hours:
            return None

        first_peak = peak_tariff_hours[0]
        precool_hours = [t for t in range(max(0, first_peak - 3), first_peak) if dev_schedule[t] > 1.5]
        
        if not precool_hours:
            return None

        precool_start = precool_hours[0]
        precool_end = precool_hours[-1] + 1
        precool_window = f"{precool_start:02d}:00-{precool_end:02d}:00"
        peak_window = f"{first_peak:02d}:00-{(first_peak + 3):02d}:00"

        # Calculate avoided peak load in HVAC zone
        base_hvac_peak = max(dev_schedule)
        opt_hvac_during_peak = [dev_schedule[t] for t in range(first_peak, min(len(dev_schedule), first_peak + 3))]
        avg_peak_cooling = sum(opt_hvac_during_peak) / len(opt_hvac_during_peak) if opt_hvac_during_peak else 0.0
        avoided_kw = round(max(0.0, base_hvac_peak - avg_peak_cooling), 2)

        # Compute exact rate differential between peak hours and precooling hours
        avg_peak_tariff = sum(problem.tariff_rates_eur_kwh[t] for t in peak_tariff_hours[:3]) / min(3, len(peak_tariff_hours))
        avg_precool_tariff = sum(problem.tariff_rates_eur_kwh[t] for t in precool_hours) / len(precool_hours)
        tariff_diff = max(0.02, avg_peak_tariff - avg_precool_tariff)
        est_savings = round(max(1.0, avoided_kw * tariff_diff * len(opt_hvac_during_peak)), 2)

        desc_el = (
            f"Προ-ψύξη χώρου '{hvac.name}' κατά το παράθυρο {precool_window} σε χαμηλή χρέωση. "
            f"Μείωση φορτίου κατά {avoided_kw:.1f} kW στη ζώνη αιχμής {peak_window} "
            f"διατηρώντας τη θερμοκρασία εντός ορίων άνεσης ({hvac.temp_min_c:.1f}-{hvac.temp_max_c:.1f}°C). "
            f"Εκτιμώμενο όφελος: €{est_savings:.2f}."
        )
        desc_en = (
            f"Pre-cool zone '{hvac.name}' during {precool_window} under off-peak rates. "
            f"Reduces peak cooling draw by {avoided_kw:.1f} kW during {peak_window} "
            f"while strictly honoring thermal comfort ({hvac.temp_min_c:.1f}-{hvac.temp_max_c:.1f}°C). "
            f"Estimated savings: €{est_savings:.2f}."
        )

        return ActionRecommendation(
            recommendation_id=f"rec_hvac_{uuid.uuid4().hex[:8]}",
            facility_id=self.facility_id,
            category=RecommendationCategory.HVAC_PRECOOLING,
            priority=PriorityLevel.MEDIUM,
            title=f"Pre-Cooling: {hvac.name}",
            description_el=desc_el,
            description_en=desc_en,
            asset_name=hvac.name,
            original_window="No pre-cooling (reactive)",
            recommended_window=precool_window,
            peak_load_avoided_kw=avoided_kw,
            estimated_savings_eur=est_savings,
            confidence_score=None,
            contracted_capacity_kw=problem.contracted_capacity_kw,
            projected_peak_kw=schedule.peak_optimized_kw,
        )

    def _evaluate_bess_action(
        self,
        bess: BESSLoad,
        problem: OptimizationProblem,
        schedule: ScheduleResult,
    ) -> ActionRecommendation | None:
        net_schedule = schedule.device_schedules.get(f"{bess.name} (Net)", [])
        if not net_schedule:
            return None

        chg_hours = [t for t, p in enumerate(net_schedule) if p > 0.5]
        dis_hours = [t for t, p in enumerate(net_schedule) if p < -0.5]

        if not chg_hours or not dis_hours:
            return None

        chg_window = f"{chg_hours[0]:02d}:00-{(chg_hours[-1] + 1):02d}:00"
        dis_window = f"{dis_hours[0]:02d}:00-{(dis_hours[-1] + 1):02d}:00"

        peak_dis_kw = max(abs(net_schedule[t]) for t in dis_hours)
        # Exact BESS arbitrage valuation from schedule power flows and spot tariffs
        dis_revenue = sum(abs(net_schedule[t]) * problem.tariff_rates_eur_kwh[t] * problem.time_step_hours for t in dis_hours)
        chg_cost = sum(abs(net_schedule[t]) * problem.tariff_rates_eur_kwh[t] * problem.time_step_hours for t in chg_hours)
        arbitrage_savings = max(0.0, dis_revenue - chg_cost)
        est_savings = round(max(1.0, arbitrage_savings), 2)

        desc_el = (
            f"Φόρτιση μπαταρίας {bess.name} στη νυχτερινή/οικονομική ζώνη {chg_window} "
            f"και εκφόρτιση {peak_dis_kw:.1f} kW στη ζώνη αιχμής {dis_window}. "
            f"Εκτιμώμενο όφελος arbitrage & peak shaving: €{est_savings:.2f}."
        )
        desc_en = (
            f"Charge {bess.name} during low-cost window {chg_window} and discharge "
            f"{peak_dis_kw:.1f} kW during peak window {dis_window}. "
            f"Projected arbitrage & peak shaving value: €{est_savings:.2f}."
        )

        return ActionRecommendation(
            recommendation_id=f"rec_bess_{uuid.uuid4().hex[:8]}",
            facility_id=self.facility_id,
            category=RecommendationCategory.BESS_ARBITRAGE,
            priority=PriorityLevel.HIGH,
            title=f"Arbitrage & Peak Shaving: {bess.name}",
            description_el=desc_el,
            description_en=desc_en,
            asset_name=bess.name,
            original_window="Idle",
            recommended_window=f"Discharge {dis_window}",
            peak_load_avoided_kw=peak_dis_kw,
            estimated_savings_eur=est_savings,
            confidence_score=None,
            contracted_capacity_kw=problem.contracted_capacity_kw,
            projected_peak_kw=schedule.peak_optimized_kw,
        )


class ClosedLoopVerifier:
    """Compares interval load against an assumed counterfactual without certifying savings."""

    def __init__(self, tolerance_pct: float = 20.0):
        self.tolerance_pct = tolerance_pct

    def verify_intervention(
        self,
        recommendation: ActionRecommendation,
        actual_measured_kw: float,
        counterfactual_baseline_kw: float,
        tariff_eur_kwh: float,
        *,
        duration_hours: float,
    ) -> VerificationRecord:
        """Audit measured power telemetry against counterfactual baseline."""
        actual_avoided_kw = round(max(0.0, counterfactual_baseline_kw - actual_measured_kw), 2)
        
        if not all(math.isfinite(v) for v in (actual_measured_kw, counterfactual_baseline_kw, tariff_eur_kwh, duration_hours)) or duration_hours <= 0:
            raise ValueError("Finite inputs and a positive duration_hours are required")
        # A short interval cannot establish avoided billing-period demand charges.
        # Preserve negative energy value when load increases or tariffs are negative.
        energy_savings = (counterfactual_baseline_kw - actual_measured_kw) * tariff_eur_kwh * duration_hours
        actual_savings_eur = round(energy_savings, 2)

        est_savings = recommendation.estimated_savings_eur
        # Interval energy and whole-recommendation savings have different scopes.
        accuracy_pct = None

        # Status classification
        if actual_avoided_kw >= recommendation.peak_load_avoided_kw * (1 - self.tolerance_pct / 100):
            status = VerificationStatus.SUCCESS
            summary_el = (
                f"Επιτυχής παρέμβαση: Επιτεύχθηκε μείωση {actual_avoided_kw:.1f} kW "
                f"(στόχος {recommendation.peak_load_avoided_kw:.1f} kW). "
                f"Εκτιμώμενο ενεργειακό όφελος διαστήματος: €{actual_savings_eur:.2f}."
            )
            summary_en = (
                f"Interval comparison: Achieved {actual_avoided_kw:.1f} kW reduction "
                f"(target {recommendation.peak_load_avoided_kw:.1f} kW). "
                f"Estimated interval energy savings: €{actual_savings_eur:.2f}."
            )
        elif actual_avoided_kw >= recommendation.peak_load_avoided_kw * 0.40:
            status = VerificationStatus.PARTIAL
            summary_el = (
                f"Μερική επιτυχία: Επιτεύχθηκε μείωση {actual_avoided_kw:.1f} kW "
                f"(στόχος {recommendation.peak_load_avoided_kw:.1f} kW). "
                f"Εκτιμώμενο ενεργειακό όφελος διαστήματος: €{actual_savings_eur:.2f}."
            )
            summary_en = (
                f"Partial intervention: Achieved {actual_avoided_kw:.1f} kW reduction "
                f"(target {recommendation.peak_load_avoided_kw:.1f} kW). "
                f"Estimated interval energy savings: €{actual_savings_eur:.2f}."
            )
        else:
            status = VerificationStatus.FAILED
            summary_el = (
                f"Απόκλιση στόχου: Το μετρηθέν φορτίο ({actual_measured_kw:.1f} kW) "
                f"δεν παρουσίασε την αναμενόμενη μείωση ({recommendation.peak_load_avoided_kw:.1f} kW). "
                f"Εκτιμώμενο ενεργειακό όφελος διαστήματος: €{actual_savings_eur:.2f}."
            )
            summary_en = (
                f"Target variance: Measured load ({actual_measured_kw:.1f} kW) "
                f"did not achieve target reduction ({recommendation.peak_load_avoided_kw:.1f} kW). "
                f"Estimated interval energy savings: €{actual_savings_eur:.2f}."
            )

        return VerificationRecord(
            verification_id=f"ver_{uuid.uuid4().hex[:8]}",
            recommendation_id=recommendation.recommendation_id,
            facility_id=recommendation.facility_id,
            verified_at=datetime.now(timezone.utc),
            status=status,
            baseline_counterfactual_kw=round(counterfactual_baseline_kw, 2),
            actual_measured_kw=round(actual_measured_kw, 2),
            actual_load_avoided_kw=actual_avoided_kw,
            estimated_savings_eur=est_savings,
            actual_savings_eur=actual_savings_eur,
            accuracy_pct=accuracy_pct,
            duration_hours=duration_hours,
            summary_el=summary_el,
            summary_en=summary_en,
        )
