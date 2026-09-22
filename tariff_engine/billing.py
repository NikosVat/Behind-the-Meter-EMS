"""
Periodic Utility Billing Engine for Greek Commercial Electricity Customers.

Implements statutory billing calculations for:
- Contracts: Γ21 (LV single-rate <= 25 kVA), Γ22 (LV dual-rate > 25 kVA), Γ23 (MV > 250 kVA).
- Retail Tariff Color Schemes: Green (Law 5068/2023 MD formula), Yellow (DAM wholesale indexed),
  Dynamic (direct hourly/weighted spot).
- Regulated Network Charges: ADMIE transmission, DEDDIE distribution, low power factor penalties
  (cos φ < 0.85), contracted capacity excess breaches.
- Public Policy Levies & Taxes: ETMEAR (LV vs MV tier), YKO, EFK excise, DETE 5‰ customs, 6% VAT.
- Financial arithmetic: Strict commercial rounding `round(x, 2)` per statutory line item.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from .contracts import TariffColor, TariffContract
from .green_tariff import calculate_green_tariff_fluctuation
from .penalties import POWER_FACTOR_THRESHOLD
from .regulated_charges import (
    ADMIE_CAPACITY_RATE_EUR_KVA_YR,
    ADMIE_ENERGY_RATE_EUR_KWH,
    DEDDIE_CAPACITY_RATE_EUR_KVA_YR,
    DEDDIE_ENERGY_RATE_EUR_KWH,
    DETE_FIXED_EQUIVALENT_EUR_KWH,
    EFK_RATE_EUR_KWH,
    ETMEAR_LV_RATE_EUR_KWH,
    ETMEAR_MV_RATE_EUR_KWH,
    VAT_RATE,
    YKO_RATE_EUR_KWH,
)


class LineItemCategory(str, Enum):
    """Aggregation categories for commercial utility bills."""
    SUPPLY = "supply"
    TRANSMISSION = "transmission"
    DISTRIBUTION = "distribution"
    REGULATED_OTHER = "regulated_other"
    TAXES = "taxes"


@dataclass(frozen=True)
class UtilityBillLineItem:
    """
    Itemized single charge, credit, or surcharge on a commercial utility bill.
    """
    code: str                  # Machine-readable code, e.g. 'SUPPLY_BASE', 'DEDDIE_PF_PENALTY'
    description: str           # Human-readable statutory description
    category: LineItemCategory # Aggregation category
    quantity: float            # Invoiced quantity (kWh, kVA, days, etc.)
    unit: str                  # Quantity unit ('kWh', 'kVA', 'days', 'month', 'EUR')
    unit_rate: float           # Statutory unit rate (€/unit)
    amount_eur: float          # Invoiced amount rounded to 2 decimal places

    def to_dict(self) -> dict[str, Any]:
        """Serializes line item to dictionary."""
        return {
            "code": self.code,
            "description": self.description,
            "category": self.category.value,
            "quantity": self.quantity,
            "unit": self.unit,
            "unit_rate": self.unit_rate,
            "amount_eur": self.amount_eur,
        }


@dataclass
class UtilityBillInput:
    """
    Input specification for calculating a periodic commercial electricity bill.
    """
    bill_id: str
    facility_id: str
    supplier_name: str
    contract_type: TariffContract | str
    tariff_color: TariffColor | str
    start_date: date
    end_date: date
    billing_period_days: int
    contracted_capacity_kva: float
    power_factor: float = 1.0
    max_demand_kva: float | None = None

    # Meter Readings (Active Energy)
    energy_active_total_kwh: float = 0.0
    energy_normal_kwh: float = 0.0
    energy_peak_kwh: float = 0.0
    energy_offpeak_kwh: float = 0.0

    # Commercial Supply Parameters
    base_supply_rate_eur_kwh: float = 0.155
    prompt_discount_percent: float = 0.0
    fixed_monthly_fee_eur: float = 5.0

    # Green Tariff Fluctuation Mechanism (Law 5068/2023)
    wholesale_tea_m1_eur_mwh: float = 115.0
    wholesale_tea_m2_eur_mwh: float | None = None
    green_lu_eur_mwh: float = 115.0
    green_ll_eur_mwh: float = 95.0
    green_alpha: float = 1.15
    green_beta: float = 0.0

    # Yellow / Dynamic Wholesale Indexing
    wholesale_tea_dam_eur_mwh: float = 115.0
    loss_factor: float = 0.135
    supplier_margin_eur_kwh: float = 0.015

    # Interval Dynamic Spot Telemetry
    hourly_consumption_kwh: list[float] | None = None
    hourly_tea_eur_mwh: list[float] | None = None
    weighted_dynamic_rate_eur_kwh: float | None = None

    # Regulatory Modifiers & Taxes
    is_medium_voltage: bool = False
    capacity_penalty_multiplier: float = 2.5
    dete_rate_eur_kwh: float = DETE_FIXED_EQUIVALENT_EUR_KWH
    vat_rate: float = VAT_RATE

    def __post_init__(self) -> None:
        if not isinstance(self.contract_type, TariffContract):
            self.contract_type = TariffContract.from_string(str(self.contract_type))
        if not isinstance(self.tariff_color, TariffColor):
            self.tariff_color = TariffColor.from_string(str(self.tariff_color))
        if self.contracted_capacity_kva <= 0.0:
            raise ValueError(f"contracted_capacity_kva must be positive: {self.contracted_capacity_kva}")
        if self.billing_period_days <= 0:
            raise ValueError(f"billing_period_days must be positive: {self.billing_period_days}")
        if self.energy_active_total_kwh < 0.0:
            raise ValueError(f"energy_active_total_kwh cannot be negative: {self.energy_active_total_kwh}")
        if self.power_factor < 0.0 or self.power_factor > 1.0:
            raise ValueError(f"power_factor must be between 0.0 and 1.0: {self.power_factor}")


@dataclass(frozen=True)
class UtilityBill:
    """
    Formal, fully itemized periodic utility bill output.
    """
    input_spec: UtilityBillInput
    line_items: list[UtilityBillLineItem]

    # Itemized Subtotals
    supply_subtotal_eur: float
    admie_subtotal_eur: float
    deddie_subtotal_eur: float
    other_regulated_subtotal_eur: float
    total_regulated_charges_eur: float

    # Pre-Tax Base, VAT, and Grand Total
    pretax_total_eur: float
    vat_amount_eur: float
    total_payable_eur: float

    def get_line_item(self, code: str) -> UtilityBillLineItem | None:
        """Find line item by exact code."""
        for item in self.line_items:
            if item.code == code:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serializes utility bill to dictionary."""
        return {
            "bill_id": self.input_spec.bill_id,
            "facility_id": self.input_spec.facility_id,
            "contract_type": self.input_spec.contract_type.value if hasattr(self.input_spec.contract_type, "value") else str(self.input_spec.contract_type),
            "tariff_color": self.input_spec.tariff_color.value if hasattr(self.input_spec.tariff_color, "value") else str(self.input_spec.tariff_color),
            "billing_period_days": self.input_spec.billing_period_days,
            "energy_active_total_kwh": self.input_spec.energy_active_total_kwh,
            "contracted_capacity_kva": self.input_spec.contracted_capacity_kva,
            "power_factor": self.input_spec.power_factor,
            "supply_subtotal_eur": self.supply_subtotal_eur,
            "admie_subtotal_eur": self.admie_subtotal_eur,
            "deddie_subtotal_eur": self.deddie_subtotal_eur,
            "other_regulated_subtotal_eur": self.other_regulated_subtotal_eur,
            "total_regulated_charges_eur": self.total_regulated_charges_eur,
            "pretax_total_eur": self.pretax_total_eur,
            "vat_amount_eur": self.vat_amount_eur,
            "total_payable_eur": self.total_payable_eur,
            "line_items": [item.to_dict() for item in self.line_items],
        }


def calculate_periodic_bill(bill_input: UtilityBillInput) -> UtilityBill:
    """
    Calculates an itemized commercial electricity bill matching Greek statutory regulations.
    Applies standard financial commercial rounding round(x, 2) to each line item.
    """
    line_items: list[UtilityBillLineItem] = []
    kwh = bill_input.energy_active_total_kwh

    # -------------------------------------------------------------------------
    # 1. Competitive Supply Charges (Ανταγωνιστικές Χρεώσεις)
    # -------------------------------------------------------------------------
    # A. Fixed Monthly Fee
    supply_fixed = round(bill_input.fixed_monthly_fee_eur, 2)
    line_items.append(
        UtilityBillLineItem(
            code="SUPPLY_FIXED",
            description="Fixed Monthly Standing Charge",
            category=LineItemCategory.SUPPLY,
            quantity=1.0,
            unit="month",
            unit_rate=bill_input.fixed_monthly_fee_eur,
            amount_eur=supply_fixed,
        )
    )

    # B. Base Energy Supply
    if bill_input.weighted_dynamic_rate_eur_kwh is not None:
        effective_rate = bill_input.weighted_dynamic_rate_eur_kwh
        supply_base = round(kwh * effective_rate, 2)
    elif bill_input.tariff_color == TariffColor.YELLOW:
        effective_rate = round(
            (bill_input.wholesale_tea_dam_eur_mwh / 1000.0) * (1.0 + bill_input.loss_factor)
            + bill_input.supplier_margin_eur_kwh
            + bill_input.base_supply_rate_eur_kwh,
            5,
        )
        supply_base = round(kwh * effective_rate, 2)
    elif bill_input.hourly_consumption_kwh and bill_input.hourly_tea_eur_mwh:
        supply_base = round(
            sum(
                h_kwh * ((h_tea / 1000.0) * (1.0 + bill_input.loss_factor) + bill_input.supplier_margin_eur_kwh + 0.020)
                for h_kwh, h_tea in zip(bill_input.hourly_consumption_kwh, bill_input.hourly_tea_eur_mwh)
            ),
            2,
        )
        effective_rate = round(supply_base / kwh, 5) if kwh > 0 else 0.0
    elif bill_input.energy_peak_kwh > 0.0 or bill_input.energy_offpeak_kwh > 0.0:
        c_peak = round(bill_input.energy_peak_kwh * (bill_input.base_supply_rate_eur_kwh * 1.25), 2)
        c_norm = round(bill_input.energy_normal_kwh * bill_input.base_supply_rate_eur_kwh, 2)
        c_off = round(bill_input.energy_offpeak_kwh * (bill_input.base_supply_rate_eur_kwh * 0.70), 2)
        supply_base = round(c_peak + c_norm + c_off, 2)
        effective_rate = bill_input.base_supply_rate_eur_kwh
    else:
        effective_rate = bill_input.base_supply_rate_eur_kwh
        supply_base = round(kwh * effective_rate, 2)

    line_items.append(
        UtilityBillLineItem(
            code="SUPPLY_BASE",
            description="Base Energy Supply Charge",
            category=LineItemCategory.SUPPLY,
            quantity=kwh,
            unit="kWh",
            unit_rate=effective_rate,
            amount_eur=supply_base,
        )
    )

    # C. Green Fluctuation Mechanism (MD Law 5068/2023)
    if bill_input.tariff_color == TariffColor.GREEN:
        md = calculate_green_tariff_fluctuation(
            tea_eur_mwh=bill_input.wholesale_tea_m1_eur_mwh,
            ll_eur_mwh=bill_input.green_ll_eur_mwh,
            lu_eur_mwh=bill_input.green_lu_eur_mwh,
            alpha=bill_input.green_alpha,
            beta=bill_input.green_beta,
            tea_m2_eur_mwh=bill_input.wholesale_tea_m2_eur_mwh,
        )
        supply_md = round(kwh * md, 2)
    else:
        md = 0.0
        supply_md = 0.0

    line_items.append(
        UtilityBillLineItem(
            code="SUPPLY_MD",
            description="Green Fluctuation Mechanism (MD Law 5068/2023)",
            category=LineItemCategory.SUPPLY,
            quantity=kwh,
            unit="kWh",
            unit_rate=round(md, 5),
            amount_eur=supply_md,
        )
    )

    # D. Prompt Payment Discount
    if bill_input.prompt_discount_percent > 0.0:
        supply_disc = round(supply_base * (bill_input.prompt_discount_percent / 100.0), 2)
    else:
        supply_disc = 0.0

    line_items.append(
        UtilityBillLineItem(
            code="SUPPLY_DISCOUNT",
            description="Prompt Payment Commercial Discount",
            category=LineItemCategory.SUPPLY,
            quantity=supply_base,
            unit="EUR",
            unit_rate=bill_input.prompt_discount_percent,
            amount_eur=supply_disc,
        )
    )

    supply_subtotal = round(supply_base + supply_fixed + supply_md - supply_disc, 2)

    # -------------------------------------------------------------------------
    # 2. ADMIE Transmission Charges (ΕΣΜΗΕ)
    # -------------------------------------------------------------------------
    admie_cap = round((ADMIE_CAPACITY_RATE_EUR_KVA_YR * bill_input.contracted_capacity_kva * bill_input.billing_period_days) / 365.0, 2)
    admie_en = round(kwh * ADMIE_ENERGY_RATE_EUR_KWH, 2)
    admie_subtotal = round(admie_cap + admie_en, 2)

    line_items.append(
        UtilityBillLineItem(
            code="ADMIE_CAPACITY",
            description="ADMIE Transmission Standing Capacity Charge",
            category=LineItemCategory.TRANSMISSION,
            quantity=bill_input.contracted_capacity_kva,
            unit="kVA",
            unit_rate=ADMIE_CAPACITY_RATE_EUR_KVA_YR,
            amount_eur=admie_cap,
        )
    )
    line_items.append(
        UtilityBillLineItem(
            code="ADMIE_ENERGY",
            description="ADMIE Transmission Energy Transport Charge",
            category=LineItemCategory.TRANSMISSION,
            quantity=kwh,
            unit="kWh",
            unit_rate=ADMIE_ENERGY_RATE_EUR_KWH,
            amount_eur=admie_en,
        )
    )

    # -------------------------------------------------------------------------
    # 3. DEDDIE Distribution Charges (ΕΔΔΗΕ)
    # -------------------------------------------------------------------------
    deddie_cap = round((DEDDIE_CAPACITY_RATE_EUR_KVA_YR * bill_input.contracted_capacity_kva * bill_input.billing_period_days) / 365.0, 2)
    deddie_en = round(kwh * DEDDIE_ENERGY_RATE_EUR_KWH, 2)

    # Power factor surcharge (cos φ < 0.85)
    pf = bill_input.power_factor
    if 0.0 < pf < POWER_FACTOR_THRESHOLD:
        m_pf = POWER_FACTOR_THRESHOLD / pf
        deddie_pf = round(deddie_en * (m_pf - 1.0), 2)
    else:
        deddie_pf = 0.0

    # Contracted capacity excess surcharge
    max_demand = bill_input.max_demand_kva
    if max_demand is not None and max_demand > bill_input.contracted_capacity_kva:
        delta_s = max_demand - bill_input.contracted_capacity_kva
        deddie_excess = round(
            bill_input.capacity_penalty_multiplier * (DEDDIE_CAPACITY_RATE_EUR_KVA_YR * delta_s * bill_input.billing_period_days) / 365.0,
            2,
        )
    else:
        deddie_excess = 0.0

    deddie_subtotal = round(deddie_cap + deddie_excess + deddie_en + deddie_pf, 2)

    line_items.append(
        UtilityBillLineItem(
            code="DEDDIE_CAPACITY_BASE",
            description="DEDDIE Distribution Standing Capacity Charge",
            category=LineItemCategory.DISTRIBUTION,
            quantity=bill_input.contracted_capacity_kva,
            unit="kVA",
            unit_rate=DEDDIE_CAPACITY_RATE_EUR_KVA_YR,
            amount_eur=deddie_cap,
        )
    )
    line_items.append(
        UtilityBillLineItem(
            code="DEDDIE_CAPACITY_EXCESS",
            description="DEDDIE Contracted Capacity Excess Surcharge",
            category=LineItemCategory.DISTRIBUTION,
            quantity=max(0.0, (max_demand or 0.0) - bill_input.contracted_capacity_kva),
            unit="kVA",
            unit_rate=round(DEDDIE_CAPACITY_RATE_EUR_KVA_YR * bill_input.capacity_penalty_multiplier, 4),
            amount_eur=deddie_excess,
        )
    )
    line_items.append(
        UtilityBillLineItem(
            code="DEDDIE_ENERGY_BASE",
            description="DEDDIE Distribution Base Energy Charge",
            category=LineItemCategory.DISTRIBUTION,
            quantity=kwh,
            unit="kWh",
            unit_rate=DEDDIE_ENERGY_RATE_EUR_KWH,
            amount_eur=deddie_en,
        )
    )
    line_items.append(
        UtilityBillLineItem(
            code="DEDDIE_PF_PENALTY",
            description="DEDDIE Low Power Factor Surcharge (cos φ < 0.85)",
            category=LineItemCategory.DISTRIBUTION,
            quantity=pf,
            unit="cos_phi",
            unit_rate=round(POWER_FACTOR_THRESHOLD / pf, 4) if (0.0 < pf < POWER_FACTOR_THRESHOLD) else 1.0,
            amount_eur=deddie_pf,
        )
    )

    # -------------------------------------------------------------------------
    # 4. Policy Levies & State Taxes
    # -------------------------------------------------------------------------
    is_mv = bill_input.is_medium_voltage or (bill_input.contract_type == TariffContract.G23)
    etmear_rate = ETMEAR_MV_RATE_EUR_KWH if is_mv else ETMEAR_LV_RATE_EUR_KWH

    etmear = round(kwh * etmear_rate, 2)
    yko = round(kwh * YKO_RATE_EUR_KWH, 2)
    efk = round(kwh * EFK_RATE_EUR_KWH, 2)
    dete = round(kwh * bill_input.dete_rate_eur_kwh, 2)

    line_items.append(
        UtilityBillLineItem(
            code="ETMEAR",
            description="ETMEAR Special Duty Supporting Renewable Energy Sources",
            category=LineItemCategory.REGULATED_OTHER,
            quantity=kwh,
            unit="kWh",
            unit_rate=etmear_rate,
            amount_eur=etmear,
        )
    )
    line_items.append(
        UtilityBillLineItem(
            code="YKO",
            description="YKO Public Service Obligations Levy",
            category=LineItemCategory.REGULATED_OTHER,
            quantity=kwh,
            unit="kWh",
            unit_rate=YKO_RATE_EUR_KWH,
            amount_eur=yko,
        )
    )
    line_items.append(
        UtilityBillLineItem(
            code="EFK",
            description="EFK Special Consumption Tax on Electricity",
            category=LineItemCategory.TAXES,
            quantity=kwh,
            unit="kWh",
            unit_rate=EFK_RATE_EUR_KWH,
            amount_eur=efk,
        )
    )
    line_items.append(
        UtilityBillLineItem(
            code="DETE",
            description="DETE Special Customs 5‰ Administration Duty",
            category=LineItemCategory.TAXES,
            quantity=kwh,
            unit="kWh",
            unit_rate=bill_input.dete_rate_eur_kwh,
            amount_eur=dete,
        )
    )

    other_regulated_subtotal = round(etmear + yko + efk + dete, 2)
    total_regulated_charges = round(admie_subtotal + deddie_subtotal + other_regulated_subtotal, 2)

    # -------------------------------------------------------------------------
    # 5. Pre-tax Base, VAT, and Total Invoiced Payable
    # -------------------------------------------------------------------------
    pretax_total = round(supply_subtotal + total_regulated_charges, 2)
    vat_amount = round(pretax_total * bill_input.vat_rate, 2)
    total_payable = round(pretax_total + vat_amount, 2)

    line_items.append(
        UtilityBillLineItem(
            code="VAT",
            description=f"Value Added Tax ({round(bill_input.vat_rate * 100, 1)}%)",
            category=LineItemCategory.TAXES,
            quantity=pretax_total,
            unit="EUR",
            unit_rate=bill_input.vat_rate,
            amount_eur=vat_amount,
        )
    )

    return UtilityBill(
        input_spec=bill_input,
        line_items=line_items,
        supply_subtotal_eur=supply_subtotal,
        admie_subtotal_eur=admie_subtotal,
        deddie_subtotal_eur=deddie_subtotal,
        other_regulated_subtotal_eur=other_regulated_subtotal,
        total_regulated_charges_eur=total_regulated_charges,
        pretax_total_eur=pretax_total,
        vat_amount_eur=vat_amount,
        total_payable_eur=total_payable,
    )
