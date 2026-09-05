from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
from typing import Any, Dict, List, Optional, Tuple
import uuid
from sqlalchemy import desc, func
from sqlalchemy.orm import Session
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.db.base import utc_now
from app.guardian.health import HealthCalculationResult, calculate_deal_health
from app.guardian.next_action import NextBestAction, determine_next_best_action
from app.models.approval import ApprovalAction, ApprovalRequest
from app.models.deal import Deal
from app.models.enums import (
    AlertStatus,
    AlertType,
    ApprovalLevel,
    ApprovalRequestStatus,
    ApprovalState,
    AuditEventType,
    DealStatus,
    FulfillmentPlanStatus,
    HealthStatus,
    NextBestActionType,
    Role,
)
from app.models.fulfillment import FulfillmentPlan, FulfillmentPlanLine
from app.models.health import DealAlert, DealHealthSnapshot, RepDiscountStats
from app.models.negotiation import NegotiationRequest
from app.models.recommendation import Recommendation
from app.models.risk import RiskAssessment, RiskFactor
from app.odoo.interface import OdooGateway
from app.schemas.health import (
    ActionQueueItem,
    ControlTowerKPIs,
    ControlTowerResponse,
    DealAlertRead,
    DealHealthDetailResponse,
    NextBestActionRead,
    RecomputeAlertsResponse,
)
from app.services import audit_service, notification_service
from app.services.deal_context_builder import quantize_dec


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def calculate_rep_discount_baseline(
    db: Session,
    odoo_user_id: int,
    sales_team_id: Optional[int] = None,
    odoo_company_id: int = 1,
    window: int = 20,
    min_sample: int = 3,
) -> Tuple[Decimal, Decimal, str, int]:
    """
    Computes average weighted discount and stddev with fallback hierarchy:
      rep history -> team history -> company-wide history (§6.13).
    Persists baseline in RepDiscountStats.
    """
    eligible_statuses = [
        DealStatus.SENT.value,
        DealStatus.UNDER_NEGOTIATION.value,
        DealStatus.CONFIRMED.value,
        DealStatus.IN_FULFILLMENT.value,
        DealStatus.FULFILLED.value,
        DealStatus.INVOICED.value,
        DealStatus.PAID.value,
    ]

    # 1. Rep history
    rep_deals = (
        db.query(Deal)
        .filter(
            Deal.owner_odoo_user_id == odoo_user_id,
            Deal.status.in_(eligible_statuses),
        )
        .order_by(desc(Deal.last_activity_at))
        .limit(window)
        .all()
    )

    baseline_source = "REP"
    sample_deals = rep_deals

    # 2. Team fallback
    if len(sample_deals) < min_sample and sales_team_id is not None:
        team_deals = (
            db.query(Deal)
            .filter(
                Deal.sales_team_odoo_id == sales_team_id,
                Deal.status.in_(eligible_statuses),
            )
            .order_by(desc(Deal.last_activity_at))
            .limit(window)
            .all()
        )
        if len(team_deals) >= min_sample:
            baseline_source = "TEAM"
            sample_deals = team_deals
        else:
            # 3. Company fallback
            company_deals = (
                db.query(Deal)
                .filter(
                    Deal.odoo_company_id == odoo_company_id,
                    Deal.status.in_(eligible_statuses),
                )
                .order_by(desc(Deal.last_activity_at))
                .limit(window)
                .all()
            )
            baseline_source = "COMPANY"
            sample_deals = company_deals

    sample_size = len(sample_deals)
    if sample_size == 0:
        return Decimal("0.00"), Decimal("0.00"), baseline_source, 0

    # Extract discount values from deals
    discounts: List[Decimal] = []
    for d in sample_deals:
        # Check if assessment exists with line snapshots
        disc_val = Decimal("0.00")
        if d.current_assessment_id:
            assessment = db.query(RiskAssessment).filter(RiskAssessment.id == d.current_assessment_id).first()
            if assessment and assessment.line_snapshot:
                total_list = Decimal("0.00")
                weighted_sum = Decimal("0.00")
                for item in assessment.line_snapshot:
                    price = Decimal(str(item.get("price_unit", 0)))
                    qty = Decimal(str(item.get("qty", 0)))
                    disc = Decimal(str(item.get("discount_pct", item.get("effective_discount", 0))))
                    list_val = price * qty
                    total_list += list_val
                    weighted_sum += list_val * disc
                if total_list > 0:
                    disc_val = quantize_dec(weighted_sum / total_list, 2)
                else:
                    disc_val = Decimal(str(d.order_discount_pct or 0))
            else:
                disc_val = Decimal(str(d.order_discount_pct or 0))
        else:
            disc_val = Decimal(str(d.order_discount_pct or 0))
        discounts.append(disc_val)

    avg_disc = sum(discounts) / Decimal(str(sample_size))
    avg_disc = quantize_dec(avg_disc, 2)

    if sample_size > 1:
        variance = sum((x - avg_disc) ** Decimal("2") for x in discounts) / Decimal(str(sample_size - 1))
        stddev = Decimal(str(math.sqrt(float(variance))))
        stddev = quantize_dec(stddev, 2)
    else:
        stddev = Decimal("0.00")

    # Persist in RepDiscountStats
    stat_rec = db.query(RepDiscountStats).filter(RepDiscountStats.odoo_user_id == odoo_user_id).first()
    if not stat_rec:
        stat_rec = RepDiscountStats(
            odoo_user_id=odoo_user_id,
            sample_size=sample_size,
            avg_weighted_discount_pct=avg_disc,
            stddev=stddev,
            baseline_source=baseline_source,
            computed_at=utc_now(),
        )
        db.add(stat_rec)
    else:
        stat_rec.sample_size = sample_size
        stat_rec.avg_weighted_discount_pct = avg_disc
        stat_rec.stddev = stddev
        stat_rec.baseline_source = baseline_source
        stat_rec.computed_at = utc_now()

    db.flush()
    return avg_disc, stddev, baseline_source, sample_size


def detect_deal_discount_anomaly(
    db: Session,
    deal: Deal,
    weighted_discount: Optional[Decimal] = None,
    anomaly_factor: Decimal = Decimal("1.5"),
    anomaly_abs_points: Decimal = Decimal("5.0"),
    anomaly_min_discount_pct: Decimal = Decimal("10.0"),
    min_sample: int = 3,
) -> Optional[str]:
    """
    Evaluates deal weighted discount against rep baseline (§6.13).
    Raises or auto-resolves DISCOUNT_ANOMALY alert. Returns severity if anomaly raised.
    """
    if weighted_discount is None:
        if deal.current_assessment_id:
            assessment = db.query(RiskAssessment).filter(RiskAssessment.id == deal.current_assessment_id).first()
            if assessment and assessment.line_snapshot:
                total_list = Decimal("0.00")
                weighted_sum = Decimal("0.00")
                for item in assessment.line_snapshot:
                    price = Decimal(str(item.get("price_unit", 0)))
                    qty = Decimal(str(item.get("qty", 0)))
                    disc = Decimal(str(item.get("discount_pct", item.get("effective_discount", 0))))
                    list_val = price * qty
                    total_list += list_val
                    weighted_sum += list_val * disc
                weighted_discount = quantize_dec(weighted_sum / total_list, 2) if total_list > 0 else Decimal(str(deal.order_discount_pct or 0))
            else:
                weighted_discount = Decimal(str(deal.order_discount_pct or 0))
        else:
            weighted_discount = Decimal(str(deal.order_discount_pct or 0))

    avg, std, source, sample_size = calculate_rep_discount_baseline(
        db=db,
        odoo_user_id=deal.owner_odoo_user_id,
        sales_team_id=deal.sales_team_odoo_id,
        odoo_company_id=deal.odoo_company_id,
        min_sample=min_sample,
    )

    if sample_size < min_sample:
        return None

    threshold = max(avg * anomaly_factor, avg + anomaly_abs_points)
    threshold = quantize_dec(threshold, 2)

    if weighted_discount >= anomaly_min_discount_pct and weighted_discount > threshold:
        is_high = weighted_discount > (avg + Decimal("2.0") * std)
        severity = "HIGH" if is_high else "MEDIUM"
        title = f"Discount anomaly: {weighted_discount:.1f}% exceeds {source} baseline {avg:.1f}%"
        detail = {
            "quote_discount": float(weighted_discount),
            "rep_avg": float(avg),
            "rep_std": float(std),
            "threshold": float(threshold),
            "sample_size": sample_size,
            "baseline_source": source,
        }

        # Upsert alert
        alert = (
            db.query(DealAlert)
            .filter(
                DealAlert.deal_id == deal.id,
                DealAlert.type == AlertType.DISCOUNT_ANOMALY.value,
                DealAlert.status == AlertStatus.OPEN.value,
            )
            .first()
        )
        if alert:
            alert.severity = severity
            alert.title = title
            alert.detail = detail
        else:
            alert = DealAlert(
                deal_id=deal.id,
                type=AlertType.DISCOUNT_ANOMALY.value,
                severity=severity,
                status=AlertStatus.OPEN.value,
                title=title,
                detail=detail,
                raised_at=utc_now(),
            )
            db.add(alert)
            db.flush()
            audit_service.record_audit_event(
                db=db,
                event_type=AuditEventType.ALERT_RAISED,
                entity_type="deal_alert",
                entity_id=str(alert.id),
                deal_id=deal.id,
                actor_type="SYSTEM",
                reason=title,
                metadata=detail,
            )
        return severity
    else:
        # Auto-resolve existing open discount anomaly alert
        open_alert = (
            db.query(DealAlert)
            .filter(
                DealAlert.deal_id == deal.id,
                DealAlert.type == AlertType.DISCOUNT_ANOMALY.value,
                DealAlert.status == AlertStatus.OPEN.value,
            )
            .first()
        )
        if open_alert:
            open_alert.status = AlertStatus.RESOLVED.value
            open_alert.resolved_at = utc_now()
            audit_service.record_audit_event(
                db=db,
                event_type=AuditEventType.ALERT_RESOLVED,
                entity_type="deal_alert",
                entity_id=str(open_alert.id),
                deal_id=deal.id,
                actor_type="SYSTEM",
                reason="Discount anomaly condition cleared.",
            )
        return None


def detect_stalled_deals(db: Session, stalled_days: int = 7) -> List[DealAlert]:
    """
    Detects deals with no activity beyond threshold (§6.12).
    Upserts OPEN STALLED_DEAL alerts and auto-resolves when cleared.
    """
    now = utc_now()
    active_statuses = [
        DealStatus.DRAFT.value,
        DealStatus.SENT.value,
        DealStatus.UNDER_NEGOTIATION.value,
    ]
    deals = db.query(Deal).filter(Deal.status.in_(active_statuses)).all()
    raised_alerts: List[DealAlert] = []

    for d in deals:
        act_dt = ensure_utc(d.last_activity_at) or now
        delta_sec = (now - act_dt).total_seconds()
        age_days = Decimal(str(max(0.0, delta_sec / 86400.0)))
        age_days = quantize_dec(age_days, 2)

        if age_days >= Decimal(str(stalled_days)):
            stalled_thresh = Decimal(str(stalled_days))
            if age_days < Decimal("2.0") * stalled_thresh:
                severity = "LOW"
            elif age_days < Decimal("4.0") * stalled_thresh:
                severity = "MEDIUM"
            else:
                severity = "HIGH"

            title = f"Deal stalled for {float(age_days):.1f} days without activity"
            detail = {
                "days_since_activity": float(age_days),
                "stalled_threshold": stalled_days,
                "last_activity_at": act_dt.isoformat(),
            }

            alert = (
                db.query(DealAlert)
                .filter(
                    DealAlert.deal_id == d.id,
                    DealAlert.type == AlertType.STALLED_DEAL.value,
                    DealAlert.status == AlertStatus.OPEN.value,
                )
                .first()
            )
            if alert:
                alert.severity = severity
                alert.title = title
                alert.detail = detail
                raised_alerts.append(alert)
            else:
                alert = DealAlert(
                    deal_id=d.id,
                    type=AlertType.STALLED_DEAL.value,
                    severity=severity,
                    status=AlertStatus.OPEN.value,
                    title=title,
                    detail=detail,
                    raised_at=now,
                )
                db.add(alert)
                db.flush()
                audit_service.record_audit_event(
                    db=db,
                    event_type=AuditEventType.ALERT_RAISED,
                    entity_type="deal_alert",
                    entity_id=str(alert.id),
                    deal_id=d.id,
                    actor_type="SYSTEM",
                    reason=title,
                    metadata=detail,
                )
                raised_alerts.append(alert)
        else:
            # Auto-resolve if open
            open_alert = (
                db.query(DealAlert)
                .filter(
                    DealAlert.deal_id == d.id,
                    DealAlert.type == AlertType.STALLED_DEAL.value,
                    DealAlert.status == AlertStatus.OPEN.value,
                )
                .first()
            )
            if open_alert:
                open_alert.status = AlertStatus.RESOLVED.value
                open_alert.resolved_at = now
                audit_service.record_audit_event(
                    db=db,
                    event_type=AuditEventType.ALERT_RESOLVED,
                    entity_type="deal_alert",
                    entity_id=str(open_alert.id),
                    deal_id=d.id,
                    actor_type="SYSTEM",
                    reason="Deal activity resumed.",
                )

    # Also resolve stalled alerts for deals that entered terminal states
    terminal_statuses = [
        DealStatus.CONFIRMED.value,
        DealStatus.IN_FULFILLMENT.value,
        DealStatus.FULFILLED.value,
        DealStatus.INVOICED.value,
        DealStatus.PAID.value,
        DealStatus.CANCELLED.value,
        DealStatus.EXPIRED.value,
    ]
    terminal_alerts = (
        db.query(DealAlert)
        .join(Deal, DealAlert.deal_id == Deal.id)
        .filter(
            Deal.status.in_(terminal_statuses),
            DealAlert.type == AlertType.STALLED_DEAL.value,
            DealAlert.status == AlertStatus.OPEN.value,
        )
        .all()
    )
    for ta in terminal_alerts:
        ta.status = AlertStatus.RESOLVED.value
        ta.resolved_at = now
        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.ALERT_RESOLVED,
            entity_type="deal_alert",
            entity_id=str(ta.id),
            deal_id=ta.deal_id,
            actor_type="SYSTEM",
            reason="Deal transitioned to confirmed/terminal status.",
        )

    db.commit()
    return raised_alerts


def detect_delivery_slippages(
    db: Session,
    gateway: OdooGateway,
    slippage_grace_days: int = 0,
) -> List[DealAlert]:
    """
    Detects delayed pickings or promised date breaches (§6.14).
    Upserts OPEN DELIVERY_SLIPPAGE alerts and auto-resolves when fulfilled.
    """
    now = utc_now()
    monitored_deals = (
        db.query(Deal)
        .filter(
            Deal.status.in_([DealStatus.CONFIRMED.value, DealStatus.IN_FULFILLMENT.value])
        )
        .all()
    )
    raised_alerts: List[DealAlert] = []

    for d in monitored_deals:
        is_slipping = False
        days_late = Decimal("0.00")
        reason_text = ""

        # Check promised delivery date
        prom_dt = ensure_utc(d.promised_delivery_date)
        if prom_dt:
            grace_deadline = prom_dt + timedelta(days=slippage_grace_days)
            if now > grace_deadline:
                is_slipping = True
                delta_late = (now - grace_deadline).total_seconds() / 86400.0
                days_late = quantize_dec(Decimal(str(delta_late)), 2)
                reason_text = f"Delivery promised for {prom_dt.strftime('%Y-%m-%d')} is {float(days_late):.1f} days overdue"

        # Check Odoo pickings
        try:
            pickings = gateway.get_pickings(d.odoo_sale_order_id)
            for p in pickings:
                if p.state not in ("done", "cancel") and p.scheduled_date:
                    try:
                        sched_dt = datetime.fromisoformat(p.scheduled_date.replace("Z", "+00:00"))
                        sched_dt = ensure_utc(sched_dt)
                        if sched_dt and now > sched_dt:
                            p_late = quantize_dec(Decimal(str((now - sched_dt).total_seconds() / 86400.0)), 2)
                            if p_late > days_late:
                                days_late = p_late
                                is_slipping = True
                                reason_text = f"Picking #{p.id} scheduled for {sched_dt.strftime('%Y-%m-%d')} is {float(days_late):.1f} days overdue"
                    except Exception:
                        pass
        except Exception:
            pass

        if is_slipping:
            severity = "HIGH" if days_late > Decimal("3.00") else "MEDIUM"
            title = reason_text or f"Delivery slippage: {float(days_late):.1f} days late"
            detail = {
                "days_late": float(days_late),
                "promised_delivery_date": prom_dt.isoformat() if prom_dt else None,
            }

            alert = (
                db.query(DealAlert)
                .filter(
                    DealAlert.deal_id == d.id,
                    DealAlert.type == AlertType.DELIVERY_SLIPPAGE.value,
                    DealAlert.status == AlertStatus.OPEN.value,
                )
                .first()
            )
            if alert:
                alert.severity = severity
                alert.title = title
                alert.detail = detail
                raised_alerts.append(alert)
            else:
                alert = DealAlert(
                    deal_id=d.id,
                    type=AlertType.DELIVERY_SLIPPAGE.value,
                    severity=severity,
                    status=AlertStatus.OPEN.value,
                    title=title,
                    detail=detail,
                    raised_at=now,
                )
                db.add(alert)
                db.flush()
                audit_service.record_audit_event(
                    db=db,
                    event_type=AuditEventType.ALERT_RAISED,
                    entity_type="deal_alert",
                    entity_id=str(alert.id),
                    deal_id=d.id,
                    actor_type="SYSTEM",
                    reason=title,
                    metadata=detail,
                )
                raised_alerts.append(alert)
        else:
            # Auto-resolve if open
            open_alert = (
                db.query(DealAlert)
                .filter(
                    DealAlert.deal_id == d.id,
                    DealAlert.type == AlertType.DELIVERY_SLIPPAGE.value,
                    DealAlert.status == AlertStatus.OPEN.value,
                )
                .first()
            )
            if open_alert:
                open_alert.status = AlertStatus.RESOLVED.value
                open_alert.resolved_at = now
                audit_service.record_audit_event(
                    db=db,
                    event_type=AuditEventType.ALERT_RESOLVED,
                    entity_type="deal_alert",
                    entity_id=str(open_alert.id),
                    deal_id=d.id,
                    actor_type="SYSTEM",
                    reason="Shipment delivered or slippage cleared.",
                )

    db.commit()
    return raised_alerts


def recompute_deal_health(
    db: Session,
    gateway: OdooGateway,
    deal: Deal,
    just_invalidated: bool = False,
    user_role: Optional[str] = None,
) -> Tuple[HealthCalculationResult, NextBestAction]:
    """
    Computes Deal Health snapshot, updates deal.health_status,
    evaluates alerts, and computes Next Best Action (§7.7, §7.8).
    """
    now = utc_now()
    act_dt = ensure_utc(deal.last_activity_at) or now
    days_since_activity = quantize_dec(
        Decimal(str(max(0.0, (now - act_dt).total_seconds() / 86400.0))), 2
    )

    # Days pending approval
    days_pending_approval = Decimal("0.00")
    if deal.approval_state in (
        ApprovalState.PENDING_MANAGER.value,
        ApprovalState.PENDING_FINANCE.value,
    ):
        pending_req = (
            db.query(ApprovalRequest)
            .filter(
                ApprovalRequest.deal_id == deal.id,
                ApprovalRequest.status == ApprovalRequestStatus.PENDING.value,
            )
            .order_by(desc(ApprovalRequest.requested_at))
            .first()
        )
        if pending_req and pending_req.requested_at:
            req_dt = ensure_utc(pending_req.requested_at)
            if req_dt:
                delta_p = (now - req_dt).total_seconds() / 86400.0
                days_pending_approval = quantize_dec(Decimal(str(max(0.0, delta_p))), 2)

    # Anomaly severity
    anomaly_severity = detect_deal_discount_anomaly(db, deal)

    # Delivery slippage alert check
    slippage_alert = (
        db.query(DealAlert)
        .filter(
            DealAlert.deal_id == deal.id,
            DealAlert.type == AlertType.DELIVERY_SLIPPAGE.value,
            DealAlert.status == AlertStatus.OPEN.value,
        )
        .first()
    )
    has_slipping_delivery = slippage_alert is not None

    # Open backorders and split fulfillment check
    has_backorder = False
    has_warehouse_split = False
    fulfillment_plan_status = None
    has_consolidatable_backorder = False

    latest_plan = (
        db.query(FulfillmentPlan)
        .filter(
            FulfillmentPlan.deal_id == deal.id,
            FulfillmentPlan.status != FulfillmentPlanStatus.SUPERSEDED.value,
        )
        .order_by(desc(FulfillmentPlan.generated_at))
        .first()
    )
    if latest_plan:
        fulfillment_plan_status = latest_plan.status
        has_backorder = any((pl.backorder_qty or 0) > 0 for pl in latest_plan.lines)
        wh_set = {pl.odoo_warehouse_id for pl in latest_plan.lines if pl.odoo_warehouse_id is not None}
        has_warehouse_split = len(wh_set) > 1

        if has_backorder:
            # Check if restocked inventory exists for backordered lines
            for pl in latest_plan.lines:
                if (pl.backorder_qty or 0) > 0:
                    avail = gateway.get_availability([pl.odoo_product_id])
                    wh_map = avail.get(pl.odoo_product_id, {})
                    if sum(wh_map.values()) >= pl.backorder_qty:
                        has_consolidatable_backorder = True
                        break

    # Open negotiation requests count
    open_neg_count = (
        db.query(func.count(NegotiationRequest.id))
        .filter(
            NegotiationRequest.deal_id == deal.id,
            NegotiationRequest.status == "OPEN",
        )
        .scalar()
        or 0
    )

    # Calculate pure health score
    calc_res = calculate_deal_health(
        deal_status=deal.status,
        days_since_activity=days_since_activity,
        stalled_threshold_days=7,
        days_pending_approval=days_pending_approval,
        anomaly_severity=anomaly_severity,
        has_slipping_delivery=has_slipping_delivery,
        has_backorder=has_backorder,
        has_warehouse_split=has_warehouse_split,
        open_negotiation_requests=open_neg_count,
    )

    # Persist DealHealthSnapshot
    snapshot = DealHealthSnapshot(
        deal_id=deal.id,
        health_status=calc_res.health_status.value,
        overall_score=calc_res.overall_score,
        stalled_score=calc_res.stalled_score,
        approval_delay_score=calc_res.approval_delay_score,
        discount_anomaly_score=calc_res.discount_anomaly_score,
        delivery_risk_score=calc_res.delivery_risk_score,
        negotiation_score=calc_res.negotiation_score,
        detail=calc_res.detail,
        calculated_at=now,
    )
    db.add(snapshot)
    deal.health_status = calc_res.health_status.value

    # Determine Next Best Action
    worst_line_id = None
    worst_line_name = None
    worst_line_ceiling = None
    max_overage = Decimal("0.00")
    margin_exposure = Decimal("0.00")

    if deal.current_assessment_id:
        assessment = db.query(RiskAssessment).filter(RiskAssessment.id == deal.current_assessment_id).first()
        if assessment:
            disc_factor = (
                db.query(RiskFactor)
                .filter(
                    RiskFactor.risk_assessment_id == assessment.id,
                    RiskFactor.factor_type == "DISCOUNT_EXCESS",
                )
                .order_by(desc(RiskFactor.contribution))
                .first()
            )
            if disc_factor and disc_factor.detail:
                worst_line_id = disc_factor.detail.get("line_id")
                worst_line_name = disc_factor.reason.split(" discount ")[0] if " discount " in disc_factor.reason else "Line"
                worst_line_ceiling = Decimal(str(disc_factor.detail.get("ceiling", 0)))
                max_overage = Decimal(str(disc_factor.detail.get("overage", 0)))

            margin_factor = (
                db.query(RiskFactor)
                .filter(
                    RiskFactor.risk_assessment_id == assessment.id,
                    RiskFactor.factor_type == "MARGIN_EXPOSURE",
                )
                .first()
            )
            if margin_factor:
                margin_exposure = margin_factor.contribution

    top_rec = (
        db.query(Recommendation)
        .filter(
            Recommendation.deal_id == deal.id,
            Recommendation.status == "ACTIVE",
        )
        .order_by(desc(Recommendation.score))
        .first()
    )
    top_rec_dict = None
    if top_rec:
        top_rec_dict = {
            "id": str(top_rec.id),
            "product_id": top_rec.odoo_product_id,
            "product_name": top_rec.product_name_cache,
            "score": float(top_rec.score),
            "projected_margin_delta": float(top_rec.margin_delta_pct),
        }

    nba = determine_next_best_action(
        deal_id=deal.id,
        approval_state=deal.approval_state,
        deal_status=deal.status,
        just_invalidated=just_invalidated,
        open_negotiation_requests=open_neg_count,
        max_overage=max_overage,
        worst_line_id=worst_line_id,
        worst_line_name=worst_line_name,
        worst_line_ceiling=worst_line_ceiling,
        margin_exposure=margin_exposure,
        top_active_recommendation=top_rec_dict,
        fulfillment_plan_status=fulfillment_plan_status,
        has_consolidatable_backorder=has_consolidatable_backorder,
        days_since_activity=int(days_since_activity),
        stalled_days=7,
        customer_confirmed_pending=deal.customer_confirmed_pending,
        user_role=user_role,
    )

    db.flush()
    return calc_res, nba


def recompute_all_alerts(db: Session, gateway: OdooGateway) -> RecomputeAlertsResponse:
    """
    Trigger immediate execution of all health and anomaly detectors (§8.7).
    """
    stalled = detect_stalled_deals(db=db)
    slippage = detect_delivery_slippages(db=db, gateway=gateway)

    # Run anomaly detector on all active deals
    active_deals = (
        db.query(Deal)
        .filter(
            Deal.status.in_([
                DealStatus.DRAFT.value,
                DealStatus.SENT.value,
                DealStatus.UNDER_NEGOTIATION.value,
                DealStatus.CONFIRMED.value,
            ])
        )
        .all()
    )
    anomaly_count = 0
    for d in active_deals:
        sev = detect_deal_discount_anomaly(db=db, deal=d)
        if sev:
            anomaly_count += 1

    db.commit()
    return RecomputeAlertsResponse(
        scanned_deals=len(active_deals),
        stalled_alerts=len(stalled),
        anomaly_alerts=anomaly_count,
        slippage_alerts=len(slippage),
        message="All alert detectors evaluated successfully.",
    )


def get_control_tower_data(db: Session) -> ControlTowerResponse:
    """
    Aggregates Control Tower dashboard KPIs and unified action queue (§8.7, §10).
    """
    now = utc_now()
    active_pipeline = (
        db.query(Deal)
        .filter(
            Deal.status.notin_([
                DealStatus.CANCELLED.value,
                DealStatus.EXPIRED.value,
            ])
        )
        .all()
    )

    pipeline_value = sum((d.amount_total_cache for d in active_pipeline), Decimal("0.00"))
    at_risk_count = sum(1 for d in active_pipeline if d.health_status == HealthStatus.AT_RISK.value)

    pending_approvals = (
        db.query(func.count(ApprovalRequest.id))
        .filter(ApprovalRequest.status == ApprovalRequestStatus.PENDING.value)
        .scalar()
        or 0
    )

    # Stalled deals count (open alerts)
    stalled_count = (
        db.query(func.count(DealAlert.id))
        .filter(
            DealAlert.type == AlertType.STALLED_DEAL.value,
            DealAlert.status == AlertStatus.OPEN.value,
        )
        .scalar()
        or 0
    )

    # Fulfillment risk count (slippage alerts + open backorders)
    slippage_count = (
        db.query(func.count(DealAlert.id))
        .filter(
            DealAlert.type == AlertType.DELIVERY_SLIPPAGE.value,
            DealAlert.status == AlertStatus.OPEN.value,
        )
        .scalar()
        or 0
    )
    backorder_deal_count = (
        db.query(func.count(func.distinct(FulfillmentPlan.deal_id)))
        .join(FulfillmentPlanLine, FulfillmentPlan.id == FulfillmentPlanLine.fulfillment_plan_id)
        .filter(
            FulfillmentPlanLine.backorder_qty > 0,
            FulfillmentPlan.status != FulfillmentPlanStatus.SUPERSEDED.value,
        )
        .scalar()
        or 0
    )
    fulfillment_risk_count = slippage_count + backorder_deal_count

    # Average approval turnaround hours
    completed_requests = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.status.in_([
                ApprovalRequestStatus.APPROVED.value,
                ApprovalRequestStatus.REJECTED.value,
                ApprovalRequestStatus.RETURNED.value,
            ]),
            ApprovalRequest.completed_at.isnot(None),
        )
        .all()
    )
    if completed_requests:
        total_hours = sum(
            (
                (
                    (ensure_utc(r.completed_at) - ensure_utc(r.requested_at)).total_seconds()
                    / 3600.0
                )
                for r in completed_requests
                if r.completed_at and r.requested_at
            ),
            0.0,
        )
        avg_approval_hours = quantize_dec(Decimal(str(total_hours / len(completed_requests))), 2)
    else:
        avg_approval_hours = Decimal("0.00")

    # Discount exposure amount
    discount_exposure = Decimal("0.00")
    for d in active_pipeline:
        if (d.order_discount_pct or 0) > 0 and (d.amount_untaxed_cache or 0) > 0:
            loss = (d.amount_untaxed_cache * d.order_discount_pct) / Decimal("100.00")
            discount_exposure += loss
    discount_exposure = quantize_dec(discount_exposure, 2)

    kpis = ControlTowerKPIs(
        pipeline_value=pipeline_value,
        at_risk_count=at_risk_count,
        pending_approvals=pending_approvals,
        discount_exposure_amount=discount_exposure,
        stalled_count=stalled_count,
        avg_approval_hours=avg_approval_hours,
        fulfillment_risk_count=fulfillment_risk_count,
    )

    # Unified Action Queue: Alerts + Pending Approvals
    action_queue: List[ActionQueueItem] = []

    # 1. Open Alerts
    open_alerts = (
        db.query(DealAlert, Deal)
        .join(Deal, DealAlert.deal_id == Deal.id)
        .filter(DealAlert.status == AlertStatus.OPEN.value)
        .order_by(desc(DealAlert.raised_at))
        .all()
    )
    for alert, deal in open_alerts:
        prio = "HIGH" if alert.severity == "HIGH" else ("MEDIUM" if alert.severity == "MEDIUM" else "LOW")
        action_queue.append(
            ActionQueueItem(
                id=str(alert.id),
                item_type="ALERT",
                deal_id=deal.id,
                deal_reference=deal.reference,
                partner_name=deal.partner_name_cache,
                title=f"[{alert.type}] {alert.title}",
                severity=alert.severity,
                priority=prio,
                deep_link=f"/deals/{deal.id}",
                created_at=ensure_utc(alert.raised_at) or now,
                detail=alert.detail or {},
            )
        )

    # 2. Pending Approvals
    pending_reqs = (
        db.query(ApprovalRequest, Deal)
        .join(Deal, ApprovalRequest.deal_id == Deal.id)
        .filter(ApprovalRequest.status == ApprovalRequestStatus.PENDING.value)
        .order_by(ApprovalRequest.requested_at.asc())
        .all()
    )
    for req, deal in pending_reqs:
        req_created = ensure_utc(req.requested_at) or now
        action_queue.append(
            ActionQueueItem(
                id=str(req.id),
                item_type="APPROVAL",
                deal_id=deal.id,
                deal_reference=deal.reference,
                partner_name=deal.partner_name_cache,
                title=f"Approval Required: {req.required_level}",
                severity="HIGH",
                priority="HIGH",
                deep_link=f"/deals/{deal.id}",
                created_at=req_created,
                detail={"required_level": req.required_level, "sequence": req.sequence},
            )
        )

    # Sort queue by Priority (HIGH -> MEDIUM -> LOW), then date
    prio_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    action_queue.sort(key=lambda x: (prio_order.get(x.priority, 3), x.created_at))

    return ControlTowerResponse(
        kpis=kpis,
        action_queue=action_queue,
        generated_at=now,
    )


def acknowledge_alert(db: Session, alert_id: uuid.UUID, user_id: int) -> DealAlert:
    alert = db.query(DealAlert).filter(DealAlert.id == alert_id).first()
    if not alert:
        raise NotFoundError("Alert not found.")

    alert.status = AlertStatus.ACKNOWLEDGED.value
    alert.acknowledged_by = user_id
    alert.acknowledged_at = utc_now()
    db.commit()
    db.refresh(alert)
    return alert


def resolve_alert(db: Session, alert_id: uuid.UUID, user_id: int) -> DealAlert:
    alert = db.query(DealAlert).filter(DealAlert.id == alert_id).first()
    if not alert:
        raise NotFoundError("Alert not found.")

    alert.status = AlertStatus.RESOLVED.value
    alert.resolved_at = utc_now()
    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.ALERT_RESOLVED,
        entity_type="deal_alert",
        entity_id=str(alert.id),
        deal_id=alert.deal_id,
        actor_type="USER",
        actor_id=user_id,
        reason="Alert manually resolved by user.",
    )
    db.commit()
    db.refresh(alert)
    return alert


def nudge_or_escalate_alert(
    db: Session,
    alert_id: uuid.UUID,
    action: str,
    message: Optional[str],
    actor_id: int,
    actor_role: str,
) -> DealAlert:
    """
    Handles NUDGE or ESCALATE actions on an alert (§6.15).
    - NUDGE: notifies deal owner rep, creates activity.
    - ESCALATE: requires SALES_MANAGER or ADMIN (or FINANCE for DELIVERY_SLIPPAGE).
      Notifies team manager, escalates severity to HIGH, audits ESCALATION_SENT.
    """
    alert = db.query(DealAlert).filter(DealAlert.id == alert_id).first()
    if not alert:
        raise NotFoundError("Alert not found.")

    deal = db.query(Deal).filter(Deal.id == alert.deal_id).first()
    if not deal:
        raise NotFoundError("Associated deal not found.")

    now = utc_now()
    if action == "NUDGE":
        # Send notification to deal owner
        notification_service.create_notification(
            db=db,
            recipient_odoo_user_id=deal.owner_odoo_user_id,
            type="NUDGE",
            title=f"Nudge on {deal.reference}: {alert.title}",
            body=message or f"Action needed on {alert.type}. Please follow up promptly.",
            entity_type="deal_alert",
            entity_id=str(alert.id),
        )
        alert.last_action = "NUDGE"
        alert.last_action_by = actor_id
        alert.last_action_at = now

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.NUDGE_SENT,
            entity_type="deal_alert",
            entity_id=str(alert.id),
            deal_id=deal.id,
            actor_type="USER",
            actor_id=actor_id,
            actor_role=actor_role,
            reason=message or "Nudge notification sent to owner rep.",
        )

    elif action == "ESCALATE":
        # RBAC Check: Manager or Admin (or Finance if DELIVERY_SLIPPAGE)
        can_escalate = actor_role in (Role.SALES_MANAGER.value, Role.ADMIN.value)
        if not can_escalate and alert.type == AlertType.DELIVERY_SLIPPAGE.value and actor_role == Role.FINANCE.value:
            can_escalate = True

        if not can_escalate:
            raise ForbiddenError("Only SALES_MANAGER or ADMIN may escalate alerts.")

        alert.severity = "HIGH"
        alert.last_action = "ESCALATE"
        alert.last_action_by = actor_id
        alert.last_action_at = now

        # Notify team manager or fall back to all managers
        manager_id = deal.sales_team_odoo_id or 2  # Default to manager1
        notification_service.create_notification(
            db=db,
            recipient_odoo_user_id=manager_id,
            type="ESCALATION",
            title=f"ESCALATION on {deal.reference}: {alert.title}",
            body=message or f"Alert on deal {deal.reference} escalated to management attention.",
            entity_type="deal_alert",
            entity_id=str(alert.id),
        )

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.ESCALATION_SENT,
            entity_type="deal_alert",
            entity_id=str(alert.id),
            deal_id=deal.id,
            actor_type="USER",
            actor_id=actor_id,
            actor_role=actor_role,
            reason=message or "Alert escalated to management.",
        )
    else:
        raise ValidationError(f"Invalid alert action: {action}. Expected NUDGE or ESCALATE.")

    db.commit()
    db.refresh(alert)
    return alert
