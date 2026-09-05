from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from app.models.enums import ApprovalLevel, RiskFactorType, RiskSeverity
from app.services.deal_context_builder import DealContext, quantize_dec


@dataclass
class RiskFactorDetail:
    factor_type: str
    source_reference: Optional[str]
    raw_value: Decimal
    weight: Decimal
    contribution: Decimal
    reason: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskCalculationResult:
    risk_score: Decimal
    severity: RiskSeverity
    required_level: ApprovalLevel
    decision: str
    factors: List[RiskFactorDetail]
    max_overage: Decimal
    weighted_overage: Decimal
    violation_ratio: Decimal
    margin_pct: Decimal
    minimum_margin_pct: Decimal


def calculate_deal_risk(
    ctx: DealContext,
    line_ceilings: Dict[int, Decimal],  # line_id -> ceiling_pct
    minimum_margin_pct: Decimal = Decimal("20.00"),
    manager_threshold: Decimal = Decimal("20.00"),
    finance_threshold: Decimal = Decimal("50.00"),
    single_line_finance_pts: Decimal = Decimal("10.00"),
    w_discount_max: Decimal = Decimal("5.0"),
    w_discount_weighted: Decimal = Decimal("2.0"),
    w_discount_ratio: Decimal = Decimal("10.0"),
    discount_cap: Decimal = Decimal("60.0"),
    w_margin: Decimal = Decimal("3.0"),
    margin_cap: Decimal = Decimal("25.0"),
    inv_split_points: Decimal = Decimal("8.0"),
    inv_backorder_points: Decimal = Decimal("20.0"),
    inv_cap: Decimal = Decimal("20.0"),
    has_open_negotiation_counter: bool = False,
    days_pending_approval: int = 0,
) -> RiskCalculationResult:
    factors: List[RiskFactorDetail] = []

    # 1. Calculate discount overages per line
    total_list = sum((l.qty * l.price_unit) for l in ctx.lines) if ctx.lines else Decimal("0.00")
    line_overages: List[Dict[str, Any]] = []

    for l in ctx.lines:
        ceiling = line_ceilings.get(l.line_id, ctx.tier_default_ceiling)
        
        # effective_i = 100 * (1 - (1 - discount_i / 100) * (1 - order_discount_pct / 100))
        # If order_discount_pct is not set separately, effective is discount_pct
        if ctx.order_discount_pct > 0:
            d_line = l.discount_pct / Decimal("100")
            d_order = ctx.order_discount_pct / Decimal("100")
            eff = Decimal("100") * (Decimal("1") - (Decimal("1") - d_line) * (Decimal("1") - d_order))
        else:
            eff = l.discount_pct

        eff = quantize_dec(eff, 4)
        overage = max(Decimal("0.00"), eff - ceiling)
        overage = quantize_dec(overage, 4)
        list_amt = l.qty * l.price_unit
        weight = (list_amt / total_list) if total_list > 0 else Decimal("0.00")

        line_overages.append({
            "line": l,
            "effective": eff,
            "ceiling": ceiling,
            "overage": overage,
            "list_amt": list_amt,
            "weight": weight,
        })

    max_overage = max((o["overage"] for o in line_overages), default=Decimal("0.00"))
    weighted_overage = sum((o["overage"] * o["weight"] for o in line_overages), Decimal("0.00"))
    violating_count = sum(1 for o in line_overages if o["overage"] > 0)
    line_count = len(ctx.lines)
    violation_ratio = (Decimal(str(violating_count)) / Decimal(str(line_count))) if line_count > 0 else Decimal("0.00")

    # DISCOUNT_EXCESS factor
    raw_disc_excess = (
        (w_discount_max * max_overage)
        + (w_discount_weighted * weighted_overage)
        + (w_discount_ratio * violation_ratio)
    )
    discount_excess_contrib = min(discount_cap, raw_disc_excess)
    discount_excess_contrib = quantize_dec(discount_excess_contrib, 2)

    if discount_excess_contrib > 0:
        # Apportion among violating lines
        violating_lines = [o for o in line_overages if o["overage"] > 0]
        sum_overage_weight = sum((o["overage"] * o["weight"] for o in violating_lines), Decimal("0.00"))

        accum_contrib = Decimal("0.00")
        max_line_entry = None
        highest_overage = Decimal("-1.0")

        for idx, o in enumerate(violating_lines):
            l = o["line"]
            if sum_overage_weight > 0:
                share = (o["overage"] * o["weight"]) / sum_overage_weight
            else:
                share = Decimal("1.0") / Decimal(str(len(violating_lines)))

            line_contrib = quantize_dec(discount_excess_contrib * share, 2)
            accum_contrib += line_contrib

            if o["overage"] > highest_overage:
                highest_overage = o["overage"]
                max_line_entry = len(factors)

            factors.append(
                RiskFactorDetail(
                    factor_type=RiskFactorType.DISCOUNT_EXCESS.value,
                    source_reference=f"line:{l.line_id}",
                    raw_value=o["overage"],
                    weight=w_discount_max,
                    contribution=line_contrib,
                    reason=(
                        f"{l.product_name} discount {o['effective']:.1f}% exceeds ceiling "
                        f"{o['ceiling']:.1f}% by {o['overage']:.1f} pts"
                    ),
                    detail={
                        "line_id": l.line_id,
                        "product_id": l.product_id,
                        "effective_discount": float(o["effective"]),
                        "ceiling": float(o["ceiling"]),
                        "overage": float(o["overage"]),
                    },
                )
            )

        # Fix rounding discrepancy onto max line
        diff = discount_excess_contrib - accum_contrib
        if diff != 0 and max_line_entry is not None:
            factors[max_line_entry].contribution += diff

    # 2. MARGIN_EXPOSURE factor
    total_net = sum(
        (o["list_amt"] * (Decimal("1") - o["effective"] / Decimal("100")))
        for o in line_overages
    )
    total_cost = sum((o["line"].cost_subtotal) for o in line_overages)
    total_margin = total_net - total_cost
    margin_pct = (total_margin / total_net * Decimal("100")) if total_net > 0 else Decimal("0.00")
    margin_pct = quantize_dec(margin_pct, 2)

    if margin_pct < minimum_margin_pct:
        margin_gap = minimum_margin_pct - margin_pct
        margin_contrib = min(margin_cap, quantize_dec(w_margin * margin_gap, 2))
        factors.append(
            RiskFactorDetail(
                factor_type=RiskFactorType.MARGIN_EXPOSURE.value,
                source_reference="deal:margin",
                raw_value=margin_gap,
                weight=w_margin,
                contribution=margin_contrib,
                reason=(
                    f"Deal margin {margin_pct:.2f}% is below minimum floor "
                    f"{minimum_margin_pct:.2f}% by {margin_gap:.2f} pts"
                ),
                detail={
                    "actual_margin_pct": float(margin_pct),
                    "minimum_margin_pct": float(minimum_margin_pct),
                    "margin_gap": float(margin_gap),
                },
            )
        )

    # 3. INVENTORY_RISK factor
    inv_points = Decimal("0.00")
    inv_reason = "Single warehouse covers demand"
    inv_shortage_lines = []

    for l in ctx.lines:
        is_stockable = str(l.product_type).upper() in ("PRODUCT", "CONSU", "STOCKABLE")
        if is_stockable:
            if l.available_qty < int(l.qty):
                inv_points = max(inv_points, inv_backorder_points)
                inv_reason = f"Insufficient stock for {l.product_name}: requested {l.qty}, available {l.available_qty}"
                inv_shortage_lines.append(l.product_name)
            elif l.inventory_shortage:
                inv_points = max(inv_points, inv_split_points)
                inv_reason = f"Multi-warehouse split required for {l.product_name}"

    inv_points = min(inv_cap, inv_points)
    if inv_points > 0:
        factors.append(
            RiskFactorDetail(
                factor_type=RiskFactorType.INVENTORY_RISK.value,
                source_reference="warehouse:inventory",
                raw_value=inv_points,
                weight=Decimal("1.0"),
                contribution=inv_points,
                reason=inv_reason,
                detail={"shortage_lines": inv_shortage_lines},
            )
        )

    # 4. NEGOTIATION_PRESSURE factor
    if has_open_negotiation_counter:
        factors.append(
            RiskFactorDetail(
                factor_type=RiskFactorType.NEGOTIATION_PRESSURE.value,
                source_reference="negotiation:counter",
                raw_value=Decimal("1.0"),
                weight=Decimal("5.0"),
                contribution=Decimal("5.00"),
                reason="Open customer counter-discount exceeds policy ceiling",
                detail={},
            )
        )

    # 5. Total Risk Score
    raw_total = sum((f.contribution for f in factors), Decimal("0.00"))
    risk_score = min(Decimal("100.00"), quantize_dec(raw_total, 2))

    # Severity Band
    if risk_score < manager_threshold:
        severity = RiskSeverity.LOW
    elif risk_score <= finance_threshold:
        severity = RiskSeverity.MEDIUM
    else:
        severity = RiskSeverity.HIGH

    # Routing Level
    if max_overage == 0 and risk_score < manager_threshold:
        required_level = ApprovalLevel.NONE
        decision = "AUTO_APPROVED"
    elif risk_score > finance_threshold or max_overage >= single_line_finance_pts:
        required_level = ApprovalLevel.MANAGER_AND_FINANCE
        decision = "FINANCE_APPROVAL_REQUIRED"
    else:
        required_level = ApprovalLevel.MANAGER
        decision = "MANAGER_APPROVAL_REQUIRED"

    return RiskCalculationResult(
        risk_score=risk_score,
        severity=severity,
        required_level=required_level,
        decision=decision,
        factors=factors,
        max_overage=max_overage,
        weighted_overage=weighted_overage,
        violation_ratio=violation_ratio,
        margin_pct=margin_pct,
        minimum_margin_pct=minimum_margin_pct,
    )
