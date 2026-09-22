"""
Automated Bill Line-Item Validator for Greek Commercial Utility Bills (Requirement R2).

Performs automated line-by-line comparison between official benchmark bills
and the Behind-the-Meter EMS calculation engine.
Asserts arithmetic discrepancy < 0.10% (or <= 0.01 EUR absolute error).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .benchmark_dataset import BENCHMARK_BILLS
from .billing import UtilityBill, calculate_periodic_bill


@dataclass(frozen=True)
class LineItemComparison:
    """Audit result for a single invoiced line item or subtotal."""
    code: str
    ground_truth_eur: float
    calculated_eur: float
    absolute_delta_eur: float
    relative_discrepancy_pct: float
    is_valid: bool
    status: str  # 'PASS' | 'FAIL'

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "ground_truth_eur": self.ground_truth_eur,
            "calculated_eur": self.calculated_eur,
            "absolute_delta_eur": self.absolute_delta_eur,
            "relative_discrepancy_pct": self.relative_discrepancy_pct,
            "is_valid": self.is_valid,
            "status": self.status,
        }


@dataclass(frozen=True)
class BillValidationResult:
    """Audit result for a complete commercial electricity bill."""
    bill_id: str
    facility_id: str
    contract_type: str
    tariff_color: str
    ground_truth_total_eur: float
    calculated_total_eur: float
    total_delta_eur: float
    total_discrepancy_pct: float
    max_line_item_discrepancy_pct: float
    mean_line_item_discrepancy_pct: float
    all_passed: bool
    line_item_comparisons: list[LineItemComparison]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bill_id": self.bill_id,
            "facility_id": self.facility_id,
            "contract_type": self.contract_type,
            "tariff_color": self.tariff_color,
            "ground_truth_total_eur": self.ground_truth_total_eur,
            "calculated_total_eur": self.calculated_total_eur,
            "total_delta_eur": self.total_delta_eur,
            "total_discrepancy_pct": self.total_discrepancy_pct,
            "max_line_item_discrepancy_pct": self.max_line_item_discrepancy_pct,
            "mean_line_item_discrepancy_pct": self.mean_line_item_discrepancy_pct,
            "all_passed": self.all_passed,
            "line_item_comparisons": [item.to_dict() for item in self.line_item_comparisons],
        }


@dataclass(frozen=True)
class BenchmarkSuiteValidationResult:
    """Audit result for the entire 9-bill benchmark suite."""
    total_bills_tested: int
    total_line_items_evaluated: int
    bills_passed: int
    bills_failed: int
    suite_passed: bool
    max_discrepancy_pct: float
    mean_discrepancy_pct: float
    bill_results: list[BillValidationResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_bills_tested": self.total_bills_tested,
            "total_line_items_evaluated": self.total_line_items_evaluated,
            "bills_passed": self.bills_passed,
            "bills_failed": self.bills_failed,
            "suite_passed": self.suite_passed,
            "max_discrepancy_pct": self.max_discrepancy_pct,
            "mean_discrepancy_pct": self.mean_discrepancy_pct,
            "bill_results": [b.to_dict() for b in self.bill_results],
        }


def _extract_calculated_mapping(bill: UtilityBill) -> dict[str, float]:
    """Maps internal codes to calculated amounts."""
    mapping: dict[str, float] = {}
    for item in bill.line_items:
        mapping[item.code] = item.amount_eur

    # Map friendly names matching benchmark expected keys
    alias_map = {
        "supply_energy_base": mapping.get("SUPPLY_BASE", 0.0),
        "supply_fixed": mapping.get("SUPPLY_FIXED", 0.0),
        "supply_md": mapping.get("SUPPLY_MD", 0.0),
        "supply_discount": mapping.get("SUPPLY_DISCOUNT", 0.0),
        "supply_subtotal": bill.supply_subtotal_eur,
        "admie_capacity": mapping.get("ADMIE_CAPACITY", 0.0),
        "admie_energy": mapping.get("ADMIE_ENERGY", 0.0),
        "admie_subtotal": bill.admie_subtotal_eur,
        "deddie_capacity": mapping.get("DEDDIE_CAPACITY_BASE", 0.0),
        "deddie_energy": mapping.get("DEDDIE_ENERGY_BASE", 0.0),
        "deddie_pf_penalty": mapping.get("DEDDIE_PF_PENALTY", 0.0),
        "deddie_cap_excess": mapping.get("DEDDIE_CAPACITY_EXCESS", 0.0),
        "deddie_subtotal": bill.deddie_subtotal_eur,
        "etmear": mapping.get("ETMEAR", 0.0),
        "yko": mapping.get("YKO", 0.0),
        "efk": mapping.get("EFK", 0.0),
        "dete": mapping.get("DETE", 0.0),
        "other_regulated_subtotal": bill.other_regulated_subtotal_eur,
        "regulated_subtotal": bill.total_regulated_charges_eur,
        "pretax_total": bill.pretax_total_eur,
        "vat_amount": bill.vat_amount_eur,
        "total_payable": bill.total_payable_eur,
    }
    return alias_map


def compare_bill(
    ground_truth_items: dict[str, float],
    calculated_bill: UtilityBill,
    tolerance_pct: float = 0.10,
    tolerance_cents_eur: float = 0.01,
) -> BillValidationResult:
    """
    Compares a calculated UtilityBill against expected ground truth line items.

    Criteria for pass per line item:
        relative_discrepancy <= tolerance_pct OR absolute_delta <= tolerance_cents_eur
    """
    calc_map = _extract_calculated_mapping(calculated_bill)
    comparisons: list[LineItemComparison] = []
    line_discrepancies: list[float] = []

    for key, expected_val in ground_truth_items.items():
        if key not in calc_map:
            continue
        calculated_val = calc_map[key]
        abs_delta = round(abs(calculated_val - expected_val), 4)

        if abs(expected_val) < 1e-6:
            rel_disc = 0.0 if abs_delta < 0.001 else round((abs_delta / 1.0) * 100.0, 4)
        else:
            rel_disc = round((abs_delta / abs(expected_val)) * 100.0, 4)

        is_valid = (rel_disc <= tolerance_pct) or (abs_delta <= tolerance_cents_eur)
        status = "PASS" if is_valid else "FAIL"

        comparisons.append(
            LineItemComparison(
                code=key,
                ground_truth_eur=round(expected_val, 2),
                calculated_eur=round(calculated_val, 2),
                absolute_delta_eur=round(abs_delta, 2),
                relative_discrepancy_pct=round(rel_disc, 4),
                is_valid=is_valid,
                status=status,
            )
        )
        line_discrepancies.append(rel_disc)

    # Total comparison
    gt_total = ground_truth_items.get("total_payable", calculated_bill.total_payable_eur)
    calc_total = calculated_bill.total_payable_eur
    total_delta = round(abs(calc_total - gt_total), 4)
    total_disc = round((total_delta / gt_total) * 100.0, 4) if gt_total > 0 else 0.0

    max_disc = max(line_discrepancies) if line_discrepancies else 0.0
    mean_disc = round(sum(line_discrepancies) / len(line_discrepancies), 4) if line_discrepancies else 0.0
    all_passed = all(item.is_valid for item in comparisons) and (
        (total_disc <= tolerance_pct) or (total_delta <= tolerance_cents_eur)
    )

    return BillValidationResult(
        bill_id=calculated_bill.input_spec.bill_id,
        facility_id=calculated_bill.input_spec.facility_id,
        contract_type=calculated_bill.input_spec.contract_type.value if hasattr(calculated_bill.input_spec.contract_type, "value") else str(calculated_bill.input_spec.contract_type),
        tariff_color=calculated_bill.input_spec.tariff_color.value if hasattr(calculated_bill.input_spec.tariff_color, "value") else str(calculated_bill.input_spec.tariff_color),
        ground_truth_total_eur=round(gt_total, 2),
        calculated_total_eur=round(calc_total, 2),
        total_delta_eur=round(total_delta, 2),
        total_discrepancy_pct=round(total_disc, 4),
        max_line_item_discrepancy_pct=round(max_disc, 4),
        mean_line_item_discrepancy_pct=round(mean_disc, 4),
        all_passed=all_passed,
        line_item_comparisons=comparisons,
    )


def validate_benchmark_suite(
    tolerance_pct: float = 0.10,
    tolerance_cents_eur: float = 0.01,
) -> BenchmarkSuiteValidationResult:
    """
    Runs automated validation across all 9 benchmark bills in BENCHMARK_BILLS.
    """
    results: list[BillValidationResult] = []
    total_items = 0

    for bill_data in BENCHMARK_BILLS.values():
        inp = bill_data["input"]
        expected = bill_data["expected"]
        calc_bill = calculate_periodic_bill(inp)
        res = compare_bill(
            ground_truth_items=expected,
            calculated_bill=calc_bill,
            tolerance_pct=tolerance_pct,
            tolerance_cents_eur=tolerance_cents_eur,
        )
        results.append(res)
        total_items += len(res.line_item_comparisons)

    bills_passed = sum(1 for r in results if r.all_passed)
    bills_failed = len(results) - bills_passed
    suite_passed = (bills_failed == 0)

    max_disc = max((r.max_line_item_discrepancy_pct for r in results), default=0.0)
    all_line_discs = [c.relative_discrepancy_pct for r in results for c in r.line_item_comparisons]
    mean_disc = round(sum(all_line_discs) / len(all_line_discs), 4) if all_line_discs else 0.0

    return BenchmarkSuiteValidationResult(
        total_bills_tested=len(results),
        total_line_items_evaluated=total_items,
        bills_passed=bills_passed,
        bills_failed=bills_failed,
        suite_passed=suite_passed,
        max_discrepancy_pct=round(max_disc, 4),
        mean_discrepancy_pct=round(mean_disc, 4),
        bill_results=results,
    )
