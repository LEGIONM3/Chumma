from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional
import uuid
from app.models.enums import ApprovalState, DealStatus, FulfillmentPlanStatus, NextBestActionType


@dataclass
class NextBestAction:
    type: str
    priority: str
    title: str
    explanation: str
    payload: Dict[str, Any] = field(default_factory=dict)
    cta_endpoint: Optional[str] = None


def determine_next_best_action(
    deal_id: Optional[uuid.UUID] = None,
    approval_state: str = ApprovalState.NOT_EVALUATED.value,
    deal_status: str = DealStatus.DRAFT.value,
    just_invalidated: bool = False,
    open_negotiation_requests: int = 0,
    max_overage: Decimal = Decimal("0.00"),
    worst_line_id: Optional[int] = None,
    worst_line_name: Optional[str] = None,
    worst_line_ceiling: Optional[Decimal] = None,
    margin_exposure: Decimal = Decimal("0.00"),
    top_active_recommendation: Optional[Dict[str, Any]] = None,
    fulfillment_plan_status: Optional[str] = None,
    has_consolidatable_backorder: bool = False,
    days_since_activity: int = 0,
    stalled_days: int = 7,
    customer_confirmed_pending: bool = False,
    user_role: Optional[str] = None,
) -> NextBestAction:
    """
    Pure-Python Next Best Action deterministic rule hierarchy (§7.8).
    First matching rule in order 1..13 wins.
    """
    deal_id_str = str(deal_id) if deal_id else ""

    # Rule 1: approval_state PENDING_FINANCE -> FINANCE_APPROVAL_REQUIRED (HIGH) [for approvers: AWAITING_APPROVER]
    if approval_state == ApprovalState.PENDING_FINANCE.value:
        is_finance_approver = user_role in ("FINANCE", "ADMIN")
        return NextBestAction(
            type=NextBestActionType.AWAITING_APPROVER.value if is_finance_approver else NextBestActionType.FINANCE_APPROVAL_REQUIRED.value,
            priority="HIGH",
            title="Finance Approval Required",
            explanation="Deal requires finance approval due to elevated risk or major line discount overage.",
            payload={"approval_state": approval_state, "required_level": "FINANCE"},
            cta_endpoint="/approvals/inbox",
        )

    # Rule 2: approval_state PENDING_MANAGER -> MANAGER_APPROVAL_REQUIRED (HIGH) [for approvers: AWAITING_APPROVER]
    if approval_state == ApprovalState.PENDING_MANAGER.value:
        is_mgr_approver = user_role in ("SALES_MANAGER", "ADMIN")
        return NextBestAction(
            type=NextBestActionType.AWAITING_APPROVER.value if is_mgr_approver else NextBestActionType.MANAGER_APPROVAL_REQUIRED.value,
            priority="HIGH",
            title="Manager Approval Required",
            explanation="Deal discount exceeds sales rep policy ceiling; manager approval required.",
            payload={"approval_state": approval_state, "required_level": "SALES_MANAGER"},
            cta_endpoint="/approvals/inbox",
        )

    # Rule 3: just INVALIDATED this evaluation -> REAPPROVAL_REQUIRED (HIGH)
    if just_invalidated or approval_state == ApprovalState.INVALIDATED.value:
        return NextBestAction(
            type=NextBestActionType.REAPPROVAL_REQUIRED.value,
            priority="HIGH",
            title="Re-approval Required",
            explanation="Deal terms were modified materially after prior approval; deal must be re-evaluated and approved.",
            payload={"deal_id": deal_id_str},
            cta_endpoint=f"/deals/{deal_id_str}/evaluate" if deal_id_str else "/deals",
        )

    # Rule 4: open negotiation requests -> RESPOND_TO_CUSTOMER (HIGH)
    if open_negotiation_requests > 0:
        return NextBestAction(
            type=NextBestActionType.RESPOND_TO_CUSTOMER.value,
            priority="HIGH",
            title="Respond to Customer Negotiation",
            explanation=f"Customer has submitted {open_negotiation_requests} open negotiation request(s) via portal.",
            payload={"open_requests_count": open_negotiation_requests},
            cta_endpoint=f"/deals/{deal_id_str}/negotiations" if deal_id_str else "/negotiations",
        )

    # Rule 5: approval_state RETURNED/REJECTED & max_overage > 0 -> REDUCE_DISCOUNT (HIGH)
    if approval_state in (ApprovalState.RETURNED.value, ApprovalState.REJECTED.value) and max_overage > 0:
        target_discount = float(worst_line_ceiling) if worst_line_ceiling is not None else 0.0
        line_desc = worst_line_name or "discounted item"
        return NextBestAction(
            type=NextBestActionType.REDUCE_DISCOUNT.value,
            priority="HIGH",
            title=f"Reduce Discount on {line_desc}",
            explanation=f"Discount on {line_desc} exceeds policy ceiling. Reduce discount to {target_discount:.1f}% to clear approval gate.",
            payload={
                "line_id": worst_line_id,
                "product_name": worst_line_name,
                "target_discount_pct": target_discount,
                "max_overage": float(max_overage),
            },
            cta_endpoint=f"/deals/{deal_id_str}" if deal_id_str else "/deals",
        )

    # Rule 6: MARGIN_EXPOSURE > 0 -> RESTORE_MARGIN (MEDIUM)
    if margin_exposure > 0:
        return NextBestAction(
            type=NextBestActionType.RESTORE_MARGIN.value,
            priority="MEDIUM",
            title="Restore Deal Margin",
            explanation="Deal gross margin is below policy floor. Attach recommended high-margin products to lift overall profitability.",
            payload={
                "margin_exposure": float(margin_exposure),
                "top_recommendation": top_active_recommendation,
            },
            cta_endpoint=f"/deals/{deal_id_str}/recommendations" if deal_id_str else "/recommendations",
        )

    # Rule 7: fulfillment plan PROPOSED & deal CONFIRMED -> ACCEPT_FULFILLMENT_PLAN (MEDIUM)
    if fulfillment_plan_status == FulfillmentPlanStatus.PROPOSED.value and deal_status == DealStatus.CONFIRMED.value:
        return NextBestAction(
            type=NextBestActionType.ACCEPT_FULFILLMENT_PLAN.value,
            priority="MEDIUM",
            title="Accept Fulfillment Plan",
            explanation="Multi-warehouse fulfillment plan proposed for confirmed deal. Accept plan to lock inventory routing.",
            payload={"fulfillment_status": fulfillment_plan_status},
            cta_endpoint=f"/deals/{deal_id_str}/fulfillment/accept" if deal_id_str else "/fulfillment",
        )

    # Rule 8: consolidatable backorder -> CONSOLIDATE_BACKORDER (MEDIUM)
    if has_consolidatable_backorder:
        return NextBestAction(
            type=NextBestActionType.CONSOLIDATE_BACKORDER.value,
            priority="MEDIUM",
            title="Consolidate Restocked Backorder",
            explanation="Warehouse inventory restocked. Consolidate outstanding backorder to complete customer shipment.",
            payload={},
            cta_endpoint=f"/deals/{deal_id_str}/fulfillment/consolidate" if deal_id_str else "/fulfillment",
        )

    # Rule 9: deal DRAFT & approval in {APPROVED, EVALUATED_NO_APPROVAL} -> SEND_TO_CUSTOMER (MEDIUM)
    if deal_status == DealStatus.DRAFT.value and approval_state in (
        ApprovalState.APPROVED.value,
        ApprovalState.EVALUATED_NO_APPROVAL.value,
    ):
        return NextBestAction(
            type=NextBestActionType.SEND_TO_CUSTOMER.value,
            priority="MEDIUM",
            title="Send Quote to Customer",
            explanation="Deal quotation is compliant and approved. Ready to be sent to customer for review.",
            payload={"approval_state": approval_state},
            cta_endpoint=f"/deals/{deal_id_str}/send" if deal_id_str else "/deals",
        )

    # Rule 10: deal SENT & days_since_activity >= stalled_days -> FOLLOW_UP_CUSTOMER (MEDIUM)
    if deal_status == DealStatus.SENT.value and days_since_activity >= stalled_days:
        return NextBestAction(
            type=NextBestActionType.FOLLOW_UP_CUSTOMER.value,
            priority="MEDIUM",
            title="Follow Up with Customer",
            explanation=f"Quotation sent {days_since_activity} days ago with no customer interaction.",
            payload={"days_since_activity": days_since_activity, "stalled_threshold": stalled_days},
            cta_endpoint=f"/deals/{deal_id_str}" if deal_id_str else "/deals",
        )

    # Rule 11: customer confirmed pending & approved -> CONFIRM_ORDER (HIGH)
    if customer_confirmed_pending and approval_state in (
        ApprovalState.APPROVED.value,
        ApprovalState.EVALUATED_NO_APPROVAL.value,
    ):
        return NextBestAction(
            type=NextBestActionType.CONFIRM_ORDER.value,
            priority="HIGH",
            title="Confirm Customer Order",
            explanation="Customer accepted quotation terms via portal. Finalize order confirmation and trigger fulfillment.",
            payload={"customer_confirmed_pending": True},
            cta_endpoint=f"/deals/{deal_id_str}/confirm" if deal_id_str else "/deals",
        )

    # Rule 12: top ACTIVE recommendation exists -> ADD_RECOMMENDATION (LOW)
    if top_active_recommendation:
        prod_name = top_active_recommendation.get("product_name", "Recommended Product")
        margin_delta = top_active_recommendation.get("projected_margin_delta", 0.0)
        return NextBestAction(
            type=NextBestActionType.ADD_RECOMMENDATION.value,
            priority="LOW",
            title=f"Add Recommendation: {prod_name}",
            explanation=f"Accretive recommendation available with {margin_delta}% margin lift.",
            payload=top_active_recommendation,
            cta_endpoint=f"/deals/{deal_id_str}/recommendations" if deal_id_str else "/recommendations",
        )

    # Rule 13: else -> NONE (LOW)
    return NextBestAction(
        type=NextBestActionType.NONE.value,
        priority="LOW",
        title="No Immediate Action Required",
        explanation="Deal is progressing normally along its lifecycle.",
        payload={},
        cta_endpoint=None,
    )
