from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid
from sqlalchemy.orm import Session
from app.core.errors import NotFoundError
from app.db.base import utc_now
from app.guardian import approval_routing, material_change, policy, risk
from app.models.deal import Deal
from app.models.enums import (
    ApprovalLevel,
    ApprovalState,
    AuditEventType,
    MaterialChangeKind,
    TriggerType,
)
from app.models.risk import RiskAssessment, RiskFactor
from app.odoo.interface import OdooGateway
from app.schemas.governance import (
    EvaluationResponse,
    RiskAssessmentRead,
    RiskFactorRead,
    WhatIfSimulateResponse,
)
from app.services import audit_service, deal_context_builder, deal_service


def evaluate_deal(
    db: Session,
    gateway: OdooGateway,
    deal_id: Optional[uuid.UUID] = None,
    odoo_sale_order_id: Optional[int] = None,
    trigger_type: TriggerType = TriggerType.MANUAL,
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
) -> EvaluationResponse:
    # 1. Resolve deal
    if deal_id:
        deal = deal_service.get_deal_by_id(db, deal_id)
        so_id = deal.odoo_sale_order_id
    elif odoo_sale_order_id:
        deal = db.query(Deal).filter(Deal.odoo_sale_order_id == odoo_sale_order_id).first()
        if not deal:
            deal = deal_service.sync_deal_from_odoo(
                db=db,
                gateway=gateway,
                odoo_sale_order_id=odoo_sale_order_id,
                actor_id=actor_id,
                actor_role=actor_role,
            )
        so_id = odoo_sale_order_id
    else:
        raise ValueError("Either deal_id or odoo_sale_order_id must be provided.")

    # 2. Build DealContext
    ctx = deal_context_builder.build_deal_context(
        db=db,
        gateway=gateway,
        order_id=so_id,
        deal_id=deal.id,
    )

    # 3. Resolve line policies and ceilings
    line_ceilings: Dict[int, Decimal] = {}
    resolved_policies_summary: Dict[str, Any] = {}
    min_margin = Decimal("20.00")
    manager_thresh = Decimal("20.00")
    finance_thresh = Decimal("50.00")
    single_line_pts = Decimal("10.00")

    for l in ctx.lines:
        res_pol = policy.resolve_policy_for_line(
            db=db,
            company_id=ctx.company_id,
            tier_code=ctx.tier_code,
            category_id=l.category_id,
            tier_default_ceiling=ctx.tier_default_ceiling,
        )
        line_ceilings[l.line_id] = res_pol.max_discount_pct
        min_margin = res_pol.minimum_margin_pct
        manager_thresh = res_pol.manager_threshold
        finance_thresh = res_pol.finance_threshold
        single_line_pts = res_pol.single_line_finance_pts
        resolved_policies_summary[str(l.line_id)] = {
            "policy_id": str(res_pol.policy_id) if res_pol.policy_id else None,
            "policy_name": res_pol.name,
            "max_discount_pct": float(res_pol.max_discount_pct),
        }

    # 4. Check for open customer negotiation counter
    from app.models.negotiation import NegotiationRequest
    open_counter = (
        db.query(NegotiationRequest)
        .filter(
            NegotiationRequest.deal_id == deal.id,
            NegotiationRequest.status == "OPEN",
            NegotiationRequest.type == "COUNTER_DISCOUNT",
        )
        .first()
    )

    # 5. Calculate Risk Score
    calc_res = risk.calculate_deal_risk(
        ctx=ctx,
        line_ceilings=line_ceilings,
        minimum_margin_pct=min_margin,
        manager_threshold=manager_thresh,
        finance_threshold=finance_thresh,
        single_line_finance_pts=single_line_pts,
        has_open_negotiation_counter=open_counter is not None,
    )

    # 6. Check material change & coverage
    prev_assessment = (
        db.query(RiskAssessment).filter(RiskAssessment.id == deal.current_assessment_id).first()
        if deal.current_assessment_id
        else None
    )
    has_change, material_kinds = material_change.is_material_change(
        old_ctx=None,  # Forced evaluation for fresh calculation
        new_ctx=ctx,
        trigger_type=trigger_type,
        previous_assessment=prev_assessment,
    )

    approved_assessment = (
        db.query(RiskAssessment).filter(RiskAssessment.id == deal.approved_assessment_id).first()
        if deal.approved_assessment_id
        else None
    )
    is_covered = material_change.is_covered_by_approved_assessment(
        approved_assessment=approved_assessment,
        new_level=calc_res.required_level,
        new_risk_score=calc_res.risk_score,
    )

    # 7. Persist Risk Assessment
    assessment = RiskAssessment(
        deal_id=deal.id,
        risk_score=calc_res.risk_score,
        severity=calc_res.severity.value,
        required_level=calc_res.required_level.value,
        decision=calc_res.decision,
        trigger_type=trigger_type.value if isinstance(trigger_type, TriggerType) else str(trigger_type),
        policy_version="v1",
        resolved_policy=resolved_policies_summary,
        line_snapshot=[
            {
                "line_id": l.line_id,
                "product_id": l.product_id,
                "product_name": l.product_name,
                "qty": float(l.qty),
                "price_unit": float(l.price_unit),
                "discount_pct": float(l.discount_pct),
                "subtotal": float(l.subtotal),
                "cost": float(l.cost_subtotal),
                "margin_pct": float(l.margin_pct),
            }
            for l in ctx.lines
        ],
        totals={
            "amount_untaxed": float(ctx.amount_untaxed),
            "amount_total": float(ctx.amount_total),
            "total_cost": float(ctx.total_cost),
            "total_margin_pct": float(ctx.total_margin_pct),
            "blended_discount_pct": float(ctx.blended_discount_pct),
        },
        calculated_at=utc_now(),
    )
    db.add(assessment)
    db.flush()

    # Persist factors
    factors_read: List[RiskFactorRead] = []
    for f in calc_res.factors:
        rf = RiskFactor(
            risk_assessment_id=assessment.id,
            factor_type=f.factor_type,
            source_reference=f.source_reference,
            raw_value=f.raw_value,
            weight=f.weight,
            contribution=f.contribution,
            reason=f.reason,
            detail=f.detail,
        )
        db.add(rf)
        factors_read.append(
            RiskFactorRead(
                factor_type=f.factor_type,
                source_reference=f.source_reference,
                raw_value=f.raw_value,
                weight=f.weight,
                contribution=f.contribution,
                reason=f.reason,
                detail=f.detail,
            )
        )

    # 8. Apply approval routing
    new_approval_state = approval_routing.apply_approval_routing(
        db=db,
        deal=deal,
        risk_assessment_id=assessment.id,
        required_level=calc_res.required_level,
        is_covered=is_covered,
        actor_id=actor_id,
        actor_role=actor_role,
    )

    # 9. Update Deal
    deal.current_risk_score = calc_res.risk_score
    deal.current_severity = calc_res.severity.value
    deal.required_level = calc_res.required_level.value
    deal.current_assessment_id = assessment.id
    deal.last_activity_at = utc_now()

    # 10. Sync governance overlay to Odoo
    is_locked = True
    if calc_res.required_level == ApprovalLevel.NONE:
        is_locked = False
    elif deal.approval_state == ApprovalState.APPROVED.value:
        is_locked = False

    try:
        gateway.set_governance(
            order_id=deal.odoo_sale_order_id,
            approval_state=deal.approval_state,
            risk_score=float(calc_res.risk_score),
            locked=is_locked,
        )
    except Exception:
        pass

    # 11. Record audit event
    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.RISK_RECALCULATED,
        entity_type="deal",
        entity_id=str(deal.id),
        deal_id=deal.id,
        actor_type="USER" if actor_id else "SYSTEM",
        actor_id=actor_id,
        actor_role=actor_role,
        reason=(
            f"Evaluated risk score {calc_res.risk_score} ({calc_res.severity.value}), "
            f"level={calc_res.required_level.value}, state={deal.approval_state}"
        ),
    )

    # 12. Evaluate recommendations
    try:
        from app.services import recommendation_service
        recommendation_service.generate_and_persist_recommendations(
            db=db,
            gateway=gateway,
            deal=deal,
            risk_assessment_id=assessment.id,
        )
    except Exception:
        pass

    # 13. Recompute Deal Health, Alerts, and Next Best Action
    try:
        from app.services import health_service
        is_invalidated = deal.approval_state == ApprovalState.INVALIDATED.value
        health_service.recompute_deal_health(
            db=db,
            gateway=gateway,
            deal=deal,
            just_invalidated=is_invalidated,
            user_role=actor_role,
        )
    except Exception:
        pass

    db.commit()
    db.refresh(deal)

    assessment_read = RiskAssessmentRead(
        id=assessment.id,
        deal_id=deal.id,
        risk_score=assessment.risk_score,
        severity=assessment.severity,
        required_level=assessment.required_level,
        decision=assessment.decision,
        trigger_type=assessment.trigger_type,
        policy_version=assessment.policy_version,
        resolved_policy=assessment.resolved_policy,
        totals=assessment.totals,
        line_snapshot=assessment.line_snapshot,
        factors=factors_read,
        calculated_at=assessment.calculated_at,
    )

    return EvaluationResponse(
        deal_id=deal.id,
        risk_assessment=assessment_read,
        approval_state=ApprovalState(deal.approval_state),
        required_level=ApprovalLevel(deal.required_level),
        decision=calc_res.decision,
        material_changes=[k.value for k in material_kinds],
    )


def simulate_deal_what_if(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
    order_discount_pct: Optional[Decimal] = None,
    line_discounts: Optional[Dict[int, Decimal]] = None,
    line_quantities: Optional[Dict[int, Decimal]] = None,
) -> WhatIfSimulateResponse:
    deal = deal_service.get_deal_by_id(db, deal_id)
    ctx = deal_context_builder.build_deal_context(
        db=db,
        gateway=gateway,
        order_id=deal.odoo_sale_order_id,
        deal_id=deal.id,
    )

    # In-memory overrides
    if order_discount_pct is not None:
        ctx.order_discount_pct = order_discount_pct

    norm_line_discounts = {int(k): Decimal(str(v)) for k, v in line_discounts.items()} if line_discounts else {}
    norm_line_quantities = {int(k): Decimal(str(v)) for k, v in line_quantities.items()} if line_quantities else {}

    line_ceilings: Dict[int, Decimal] = {}
    for l in ctx.lines:
        res_pol = policy.resolve_policy_for_line(
            db=db,
            company_id=ctx.company_id,
            tier_code=ctx.tier_code,
            category_id=l.category_id,
            tier_default_ceiling=ctx.tier_default_ceiling,
        )
        line_ceilings[l.line_id] = res_pol.max_discount_pct

        if l.line_id in norm_line_discounts:
            l.discount_pct = norm_line_discounts[l.line_id]
        if l.line_id in norm_line_quantities:
            l.qty = norm_line_quantities[l.line_id]

    calc_res = risk.calculate_deal_risk(
        ctx=ctx,
        line_ceilings=line_ceilings,
    )

    factors_read = [
        RiskFactorRead(
            factor_type=f.factor_type,
            source_reference=f.source_reference,
            raw_value=f.raw_value,
            weight=f.weight,
            contribution=f.contribution,
            reason=f.reason,
            detail=f.detail,
        )
        for f in calc_res.factors
    ]

    return WhatIfSimulateResponse(
        simulated_risk_score=calc_res.risk_score,
        simulated_severity=calc_res.severity.value,
        simulated_required_level=calc_res.required_level,
        simulated_decision=calc_res.decision,
        factors=factors_read,
        margin_pct=calc_res.margin_pct,
        amount_total=ctx.amount_total,
    )
