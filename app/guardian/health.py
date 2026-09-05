from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional
from app.models.enums import HealthStatus
from app.services.deal_context_builder import quantize_dec

TERMINAL_DEAL_STATUSES = {
    "CONFIRMED",
    "IN_FULFILLMENT",
    "FULFILLED",
    "INVOICED",
    "PAID",
    "CANCELLED",
    "EXPIRED",
}


@dataclass
class HealthCalculationResult:
    overall_score: Decimal
    health_status: HealthStatus
    stalled_score: Decimal
    approval_delay_score: Decimal
    discount_anomaly_score: Decimal
    delivery_risk_score: Decimal
    negotiation_score: Decimal
    detail: Dict[str, Any] = field(default_factory=dict)


def calculate_deal_health(
    deal_status: str,
    days_since_activity: Decimal = Decimal("0.00"),
    stalled_threshold_days: int = 7,
    days_pending_approval: Decimal = Decimal("0.00"),
    anomaly_severity: Optional[str] = None,
    has_slipping_delivery: bool = False,
    has_backorder: bool = False,
    has_warehouse_split: bool = False,
    open_negotiation_requests: int = 0,
) -> HealthCalculationResult:
    """
    Pure-Python Deal Health score calculation engine (§7.7).

    Formulas:
      - stalled_score          = min(40, 40 * days_since_activity / (2 * stalled_days))
                                 (0 if deal CONFIRMED or later)
      - approval_delay_score   = min(20, 5 * days_pending_approval)
      - discount_anomaly_score = 25 if anomaly HIGH, 15 if MEDIUM, else 0
      - delivery_risk_score    = 30 if any picking past promised date, 20 if backorder, 10 if split, else 0
      - negotiation_score      = min(15, 5 * open negotiation requests)
      - overall                = min(100, sum)
      - health_status          = HEALTHY <= 30, WATCH 31-60, AT_RISK >= 61
    """
    # 1. Stalled score
    stalled_thresh = Decimal(str(max(1, stalled_threshold_days)))
    days_activity = max(Decimal("0.00"), quantize_dec(Decimal(str(days_since_activity)), 2))

    if deal_status in TERMINAL_DEAL_STATUSES:
        stalled_score = Decimal("0.00")
    else:
        raw_stalled = (Decimal("40.00") * days_activity) / (Decimal("2.0") * stalled_thresh)
        stalled_score = min(Decimal("40.00"), max(Decimal("0.00"), quantize_dec(raw_stalled, 2)))

    # 2. Approval delay score
    days_pending = max(Decimal("0.00"), quantize_dec(Decimal(str(days_pending_approval)), 2))
    raw_approval_delay = Decimal("5.00") * days_pending
    approval_delay_score = min(Decimal("20.00"), max(Decimal("0.00"), quantize_dec(raw_approval_delay, 2)))

    # 3. Discount anomaly score
    if anomaly_severity == "HIGH":
        discount_anomaly_score = Decimal("25.00")
    elif anomaly_severity == "MEDIUM":
        discount_anomaly_score = Decimal("15.00")
    else:
        discount_anomaly_score = Decimal("0.00")

    # 4. Delivery risk score
    if has_slipping_delivery:
        delivery_risk_score = Decimal("30.00")
    elif has_backorder:
        delivery_risk_score = Decimal("20.00")
    elif has_warehouse_split:
        delivery_risk_score = Decimal("10.00")
    else:
        delivery_risk_score = Decimal("0.00")

    # 5. Negotiation score
    open_reqs = max(0, int(open_negotiation_requests))
    raw_neg = Decimal("5.00") * Decimal(str(open_reqs))
    negotiation_score = min(Decimal("15.00"), quantize_dec(raw_neg, 2))

    # 6. Overall blended penalty score
    raw_total = (
        stalled_score
        + approval_delay_score
        + discount_anomaly_score
        + delivery_risk_score
        + negotiation_score
    )
    overall_score = min(Decimal("100.00"), quantize_dec(raw_total, 2))

    # 7. Health status mapping
    if overall_score <= Decimal("30.00"):
        health_status = HealthStatus.HEALTHY
    elif overall_score <= Decimal("60.00"):
        health_status = HealthStatus.WATCH
    else:
        health_status = HealthStatus.AT_RISK

    detail = {
        "stalled_days": float(days_activity),
        "stalled_threshold": int(stalled_thresh),
        "days_pending_approval": float(days_pending),
        "anomaly_severity": anomaly_severity,
        "has_slipping_delivery": has_slipping_delivery,
        "has_backorder": has_backorder,
        "has_warehouse_split": has_warehouse_split,
        "open_negotiation_requests": open_reqs,
    }

    return HealthCalculationResult(
        overall_score=overall_score,
        health_status=health_status,
        stalled_score=stalled_score,
        approval_delay_score=approval_delay_score,
        discount_anomaly_score=discount_anomaly_score,
        delivery_risk_score=delivery_risk_score,
        negotiation_score=negotiation_score,
        detail=detail,
    )
