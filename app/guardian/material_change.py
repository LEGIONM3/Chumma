from decimal import Decimal
from typing import List, Optional, Tuple
from app.models.enums import ApprovalLevel, MaterialChangeKind, TriggerType
from app.models.risk import RiskAssessment
from app.services.deal_context_builder import DealContext


def is_material_change(
    old_ctx: Optional[DealContext],
    new_ctx: DealContext,
    trigger_type: TriggerType = TriggerType.MANUAL,
    previous_assessment: Optional[RiskAssessment] = None,
) -> Tuple[bool, List[MaterialChangeKind]]:
    if old_ctx is None or previous_assessment is None:
        return True, [MaterialChangeKind.DISCOUNT]

    changes: List[MaterialChangeKind] = []

    # 1. Discount change
    if abs(old_ctx.order_discount_pct - new_ctx.order_discount_pct) > Decimal("0.01"):
        changes.append(MaterialChangeKind.DISCOUNT)

    old_lines_map = {l.line_id: l for l in old_ctx.lines}
    new_lines_map = {l.line_id: l for l in new_ctx.lines}

    # 2. Line addition / removal
    added_ids = set(new_lines_map.keys()) - set(old_lines_map.keys())
    removed_ids = set(old_lines_map.keys()) - set(new_lines_map.keys())
    if added_ids:
        changes.append(MaterialChangeKind.LINE_ADDED)
    if removed_ids:
        changes.append(MaterialChangeKind.LINE_REMOVED)

    # 3. Line details (qty, discount, recurring)
    for lid, new_l in new_lines_map.items():
        if lid in old_lines_map:
            old_l = old_lines_map[lid]
            if abs(new_l.qty - old_l.qty) > Decimal("0.001"):
                if MaterialChangeKind.QUANTITY not in changes:
                    changes.append(MaterialChangeKind.QUANTITY)
            if abs(new_l.discount_pct - old_l.discount_pct) > Decimal("0.01"):
                if MaterialChangeKind.DISCOUNT not in changes:
                    changes.append(MaterialChangeKind.DISCOUNT)
            if new_l.is_recurring and (
                new_l.price_unit != old_l.price_unit
                or new_l.qty != old_l.qty
                or new_l.discount_pct != old_l.discount_pct
            ):
                if MaterialChangeKind.SUBSCRIPTION_TERMS not in changes:
                    changes.append(MaterialChangeKind.SUBSCRIPTION_TERMS)

    # 4. Customer counter trigger
    if trigger_type == TriggerType.CUSTOMER_NEGOTIATED:
        changes.append(MaterialChangeKind.CUSTOMER_COUNTER)

    # 5. Inventory / Stock changes
    if trigger_type == TriggerType.STOCK_CHANGED:
        old_shortage = any(l.inventory_shortage for l in old_ctx.lines)
        new_shortage = any(l.inventory_shortage for l in new_ctx.lines)
        if new_shortage and not old_shortage:
            changes.append(MaterialChangeKind.FULFILLMENT_RISK)

    return (len(changes) > 0, changes)


LEVEL_PRIORITY = {
    ApprovalLevel.NONE.value: 0,
    ApprovalLevel.MANAGER.value: 1,
    ApprovalLevel.MANAGER_AND_FINANCE.value: 2,
}


def is_covered_by_approved_assessment(
    approved_assessment: Optional[RiskAssessment],
    new_level: ApprovalLevel,
    new_risk_score: Decimal,
) -> bool:
    if not approved_assessment:
        return False

    app_lvl = approved_assessment.required_level
    new_lvl = new_level.value

    if LEVEL_PRIORITY.get(new_lvl, 99) > LEVEL_PRIORITY.get(app_lvl, -1):
        return False

    if new_risk_score > approved_assessment.risk_score:
        return False

    return True
