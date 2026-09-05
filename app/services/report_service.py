from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models.approval import ApprovalAction, ApprovalRequest
from app.models.deal import Deal
from app.models.enums import (
    AlertStatus,
    AlertType,
    ApprovalLevel,
    ApprovalRequestStatus,
    ApprovalState,
    DealStatus,
    RiskSeverity,
)
from app.models.fulfillment import FulfillmentPlan, FulfillmentPlanLine, WarehouseProfile
from app.models.health import DealAlert, RepDiscountStats
from app.models.identity import DealflowUser
from app.models.risk import RiskAssessment, RiskFactor
from app.odoo.interface import OdooGateway
from app.schemas.reports import (
    ApprovalStageTurnaround,
    ApprovalStatusBreakdown,
    PipelineStageItem,
    ProductPerformanceItem,
    RejectionReasonCount,
    RepDiscountPerformanceItem,
    ReportApprovalsResponse,
    ReportBillingResponse,
    ReportDealItem,
    ReportDealsResponse,
    ReportDiscountsResponse,
    ReportFulfillmentResponse,
    ReportPipelineResponse,
    ReportProductsResponse,
    ReportSummaryResponse,
    RiskSeverityBreakdown,
    TopRiskFactorItem,
    WarehouseFulfillmentItem,
)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def strip_tz(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


class ReportService:
    def __init__(self, db: Session, gateway: Optional[OdooGateway] = None) -> None:
        self.db = db
        self.gateway = gateway

    def parse_filters(
        self,
        period: Optional[str] = "month",
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        team_id: Optional[int] = None,
        rep_id: Optional[int] = None,
        approval_status: Optional[str] = None,
        product_id: Optional[int] = None,
        category_id: Optional[int] = None,
        fmt: Optional[str] = "json",
    ) -> Dict[str, Any]:
        now = utc_now()
        resolved_from = from_date
        resolved_to = to_date

        p = (period or "").lower()
        if not resolved_from and not resolved_to:
            if p == "today":
                resolved_from = now.replace(hour=0, minute=0, second=0, microsecond=0)
                resolved_to = now
            elif p == "week":
                resolved_from = now - timedelta(days=7)
                resolved_to = now
            elif p == "month":
                resolved_from = now - timedelta(days=30)
                resolved_to = now
            elif p == "quarter":
                resolved_from = now - timedelta(days=90)
                resolved_to = now
            elif p == "custom":
                resolved_from = None
                resolved_to = None

        return {
            "period": period or "custom",
            "from_date": resolved_from,
            "to_date": resolved_to,
            "team_id": team_id,
            "rep_id": rep_id,
            "approval_status": (approval_status or "").lower() if approval_status else None,
            "product_id": product_id,
            "category_id": category_id,
            "format": (fmt or "json").lower(),
        }

    def _get_matching_deals(self, filters: Dict[str, Any]) -> List[Deal]:
        query = self.db.query(Deal)

        from_date = filters.get("from_date")
        to_date = filters.get("to_date")
        if from_date:
            query = query.filter(Deal.created_at >= strip_tz(from_date))
        if to_date:
            query = query.filter(Deal.created_at <= strip_tz(to_date))

        if filters.get("team_id"):
            query = query.filter(Deal.sales_team_odoo_id == filters["team_id"])
        if filters.get("rep_id"):
            query = query.filter(Deal.owner_odoo_user_id == filters["rep_id"])

        app_status = filters.get("approval_status")
        if app_status:
            if app_status in ("pending", "pending_approval"):
                query = query.filter(
                    Deal.approval_state.in_([
                        ApprovalState.PENDING_MANAGER.value,
                        ApprovalState.PENDING_FINANCE.value,
                    ])
                )
            elif app_status in ("approved",):
                query = query.filter(
                    or_(
                        Deal.approval_state == ApprovalState.APPROVED.value,
                        Deal.approved_assessment_id.isnot(None),
                    )
                )
            elif app_status in ("rejected",):
                query = query.filter(Deal.approval_state == ApprovalState.REJECTED.value)
            elif app_status in ("none", "evaluated_no_approval"):
                query = query.filter(
                    or_(
                        Deal.approval_state == ApprovalState.EVALUATED_NO_APPROVAL.value,
                        Deal.approval_state == ApprovalState.NOT_EVALUATED.value,
                        (
                            (Deal.required_level == ApprovalLevel.NONE.value)
                            & (~Deal.approval_state.in_([
                                ApprovalState.PENDING_MANAGER.value,
                                ApprovalState.PENDING_FINANCE.value,
                                ApprovalState.APPROVED.value,
                                ApprovalState.REJECTED.value,
                                ApprovalState.RETURNED.value,
                            ]))
                        ),
                    )
                )
            else:
                # Direct match fallback
                query = query.filter(Deal.approval_state == app_status.upper())

        deals = query.order_by(Deal.created_at.desc()).all()

        # Product / Category filter in deal line snapshots
        pid = filters.get("product_id")
        cid = filters.get("category_id")
        if pid or cid:
            filtered_deals = []
            for d in deals:
                if not d.current_assessment_id:
                    continue
                ass = self.db.query(RiskAssessment).filter(RiskAssessment.id == d.current_assessment_id).first()
                if not ass or not ass.line_snapshot:
                    continue
                matched = False
                for line in ass.line_snapshot:
                    if pid and line.get("product_id") == pid:
                        matched = True
                        break
                    if cid and (line.get("category_id") == cid or cid in line.get("category_path", [])):
                        matched = True
                        break
                if matched:
                    filtered_deals.append(d)
            return filtered_deals

        return deals

    def get_summary_report(self, filters: Dict[str, Any]) -> ReportSummaryResponse:
        deals = self._get_matching_deals(filters)

        created_count = len(deals)
        sent_deals = [
            d for d in deals
            if d.sent_at is not None or d.status in (
                DealStatus.SENT.value,
                DealStatus.UNDER_NEGOTIATION.value,
                DealStatus.CONFIRMED.value,
                DealStatus.IN_FULFILLMENT.value,
                DealStatus.FULFILLED.value,
                DealStatus.INVOICED.value,
                DealStatus.PAID.value,
            )
        ]
        sent_count = len(sent_deals)

        confirmed_deals = [
            d for d in deals
            if d.confirmed_at is not None or d.status in (
                DealStatus.CONFIRMED.value,
                DealStatus.IN_FULFILLMENT.value,
                DealStatus.FULFILLED.value,
                DealStatus.INVOICED.value,
                DealStatus.PAID.value,
            )
        ]
        confirmed_count = len(confirmed_deals)

        win_rate = Decimal("0.00")
        if created_count > 0:
            win_rate = Decimal(str(round((confirmed_count / created_count) * 100.0, 2)))

        confirmed_rev = Decimal("0.00")
        for d in confirmed_deals:
            rev = (d.one_time_total_cache or Decimal("0.00")) + (d.recurring_first_cycle_total_cache or Decimal("0.00"))
            if rev == Decimal("0.00") and d.amount_total_cache:
                rev = d.amount_total_cache
            confirmed_rev += rev

        avg_disc = Decimal("0.00")
        avg_margin = Decimal("0.00")
        if created_count > 0:
            total_disc = sum((d.order_discount_pct or Decimal("0.00")) for d in deals)
            total_margin = sum((d.margin_pct_cache or Decimal("0.00")) for d in deals)
            avg_disc = Decimal(str(round(total_disc / created_count, 2)))
            avg_margin = Decimal(str(round(total_margin / created_count, 2)))

        # Approval turnaround
        deal_ids = [d.id for d in deals]
        avg_turnaround_hours = Decimal("0.00")
        pending_approvals = 0

        if deal_ids:
            reqs = (
                self.db.query(ApprovalRequest)
                .filter(ApprovalRequest.deal_id.in_(deal_ids))
                .all()
            )
            completed_hours = []
            for r in reqs:
                if r.status == ApprovalRequestStatus.PENDING.value:
                    pending_approvals += 1
                elif r.completed_at and r.requested_at:
                    req_start = ensure_utc(r.requested_at)
                    req_end = ensure_utc(r.completed_at)
                    diff_hours = (req_end - req_start).total_seconds() / 3600.0
                    completed_hours.append(diff_hours)

            if completed_hours:
                avg_turnaround_hours = Decimal(str(round(sum(completed_hours) / len(completed_hours), 2)))
        else:
            # Check deals in pending approval state
            pending_approvals = len([
                d for d in deals
                if d.approval_state in (ApprovalState.PENDING_MANAGER.value, ApprovalState.PENDING_FINANCE.value)
            ])

        return ReportSummaryResponse(
            period=filters.get("period", "month"),
            date_from=filters.get("from_date"),
            date_to=filters.get("to_date"),
            quotations_created=created_count,
            quotations_sent=sent_count,
            quotations_confirmed=confirmed_count,
            win_rate=win_rate,
            revenue_confirmed=confirmed_rev,
            avg_discount=avg_disc,
            avg_margin=avg_margin,
            avg_approval_turnaround_hours=avg_turnaround_hours,
            pending_approvals_count=pending_approvals,
            generated_at=utc_now(),
        )

    def get_deals_report(self, filters: Dict[str, Any]) -> ReportDealsResponse:
        deals = self._get_matching_deals(filters)
        items: List[ReportDealItem] = []

        for d in deals:
            items.append(
                ReportDealItem(
                    deal_id=str(d.id),
                    reference=d.reference,
                    odoo_order_name=d.odoo_order_name,
                    partner_name=d.partner_name_cache or f"Partner #{d.odoo_partner_id}",
                    owner_odoo_user_id=d.owner_odoo_user_id,
                    sales_team_odoo_id=d.sales_team_odoo_id,
                    status=d.status,
                    approval_state=d.approval_state,
                    health_status=d.health_status,
                    current_risk_score=d.current_risk_score,
                    current_severity=d.current_severity,
                    amount_total=d.amount_total_cache or Decimal("0.00"),
                    order_discount_pct=d.order_discount_pct or Decimal("0.00"),
                    margin_pct=d.margin_pct_cache or Decimal("0.00"),
                    created_at=d.created_at,
                    sent_at=d.sent_at,
                    confirmed_at=d.confirmed_at,
                )
            )

        return ReportDealsResponse(
            items=items,
            total=len(items),
            period=filters.get("period", "month"),
            generated_at=utc_now(),
        )

    def get_approvals_report(self, filters: Dict[str, Any]) -> ReportApprovalsResponse:
        deals = self._get_matching_deals(filters)
        deal_ids = [d.id for d in deals]

        query = self.db.query(ApprovalRequest)
        if deal_ids:
            query = query.filter(ApprovalRequest.deal_id.in_(deal_ids))
        elif filters.get("team_id") or filters.get("rep_id") or filters.get("from_date"):
            # No deals match filters
            return ReportApprovalsResponse(
                by_status=[],
                turnaround_by_stage=[],
                top_rejection_reasons=[],
                total_requests=0,
                completed_requests=0,
                approval_rate=Decimal("0.00"),
                period=filters.get("period", "month"),
                generated_at=utc_now(),
            )

        requests = query.all()

        # Status breakdown
        status_counts: Dict[str, int] = defaultdict(int)
        for r in requests:
            status_counts[r.status] += 1

        by_status = [
            ApprovalStatusBreakdown(status=s, count=c)
            for s, c in sorted(status_counts.items(), key=lambda x: x[0])
        ]

        # Stage turnaround
        stage_groups: Dict[str, List[ApprovalRequest]] = defaultdict(list)
        for r in requests:
            stage_groups[r.required_level].append(r)

        turnaround_by_stage: List[ApprovalStageTurnaround] = []
        for stage, req_list in stage_groups.items():
            completed_hours = []
            approved_count = 0
            rejected_count = 0
            returned_count = 0

            for r in req_list:
                if r.status == ApprovalRequestStatus.APPROVED.value:
                    approved_count += 1
                elif r.status == ApprovalRequestStatus.REJECTED.value:
                    rejected_count += 1
                elif r.status == ApprovalRequestStatus.RETURNED.value:
                    returned_count += 1

                if r.completed_at and r.requested_at:
                    start_dt = ensure_utc(r.requested_at)
                    end_dt = ensure_utc(r.completed_at)
                    completed_hours.append((end_dt - start_dt).total_seconds() / 3600.0)

            avg_hrs = Decimal("0.00")
            if completed_hours:
                avg_hrs = Decimal(str(round(sum(completed_hours) / len(completed_hours), 2)))

            turnaround_by_stage.append(
                ApprovalStageTurnaround(
                    required_level=stage,
                    total_requests=len(req_list),
                    avg_turnaround_hours=avg_hrs,
                    approved_count=approved_count,
                    rejected_count=rejected_count,
                    returned_count=returned_count,
                )
            )

        # Top rejection reasons
        reason_counts: Dict[str, int] = defaultdict(int)
        for r in requests:
            if r.status in (ApprovalRequestStatus.REJECTED.value, ApprovalRequestStatus.RETURNED.value) and r.decision_reason:
                reason_counts[r.decision_reason.strip()] += 1

        # Also check ApprovalAction reasons
        if requests:
            req_ids = [r.id for r in requests]
            actions = (
                self.db.query(ApprovalAction)
                .filter(
                    ApprovalAction.approval_request_id.in_(req_ids),
                    ApprovalAction.action.in_(["REJECT", "RETURN"]),
                )
                .all()
            )
            for a in actions:
                if a.reason and a.reason.strip():
                    reason_counts[a.reason.strip()] += 1

        top_rejections = [
            RejectionReasonCount(reason=reason, count=count)
            for reason, count in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        ]

        total_reqs = len(requests)
        completed_reqs = sum(1 for r in requests if r.completed_at is not None)
        approved_reqs = sum(1 for r in requests if r.status == ApprovalRequestStatus.APPROVED.value)
        approval_rate = Decimal("0.00")
        if completed_reqs > 0:
            approval_rate = Decimal(str(round((approved_reqs / completed_reqs) * 100.0, 2)))

        return ReportApprovalsResponse(
            by_status=by_status,
            turnaround_by_stage=turnaround_by_stage,
            top_rejection_reasons=top_rejections,
            total_requests=total_reqs,
            completed_requests=completed_reqs,
            approval_rate=approval_rate,
            period=filters.get("period", "month"),
            generated_at=utc_now(),
        )

    def get_products_report(self, filters: Dict[str, Any]) -> ReportProductsResponse:
        deals = self._get_matching_deals(filters)

        product_aggregates: Dict[int, Dict[str, Any]] = defaultdict(
            lambda: {"name": "", "category_id": None, "qty": 0.0, "revenue": Decimal("0.00"), "discounts": []}
        )

        for d in deals:
            if not d.current_assessment_id:
                continue
            ass = self.db.query(RiskAssessment).filter(RiskAssessment.id == d.current_assessment_id).first()
            if not ass or not ass.line_snapshot:
                continue

            for line in ass.line_snapshot:
                pid = line.get("product_id")
                if not pid:
                    continue
                if filters.get("product_id") and pid != filters["product_id"]:
                    continue
                cid = line.get("category_id")
                if filters.get("category_id") and cid != filters["category_id"] and filters["category_id"] not in line.get("category_path", []):
                    continue

                pname = line.get("product_name", f"Product #{pid}")
                qty = float(line.get("qty", 1.0))
                price_unit = Decimal(str(line.get("price_unit", 0.0)))
                disc_pct = Decimal(str(line.get("discount_pct", 0.0)))
                net_rev = (price_unit * Decimal(str(qty))) * (Decimal("1.00") - (disc_pct / Decimal("100.00")))

                agg = product_aggregates[pid]
                agg["name"] = pname
                agg["category_id"] = cid
                agg["qty"] += qty
                agg["revenue"] += net_rev
                agg["discounts"].append(disc_pct)

        # Also enrich from gateway confirmed orders if available and sparse
        if self.gateway and not product_aggregates:
            try:
                gw_products = self.gateway.get_products(list(range(101, 110)))
                for pid, p in gw_products.items():
                    product_aggregates[pid] = {
                        "name": p.name,
                        "category_id": p.category_id,
                        "qty": 0.0,
                        "revenue": Decimal("0.00"),
                        "discounts": [Decimal("0.00")],
                    }
            except Exception:
                pass

        items: List[ProductPerformanceItem] = []
        for pid, data in product_aggregates.items():
            avg_disc = Decimal("0.00")
            if data["discounts"]:
                avg_disc = Decimal(str(round(sum(data["discounts"]) / len(data["discounts"]), 2)))
            items.append(
                ProductPerformanceItem(
                    product_id=pid,
                    product_name=data["name"] or f"Product #{pid}",
                    category_id=data["category_id"],
                    qty_sold=data["qty"],
                    revenue=data["revenue"],
                    avg_discount_pct=avg_disc,
                )
            )

        best_qty = sorted(items, key=lambda x: x.qty_sold, reverse=True)
        best_rev = sorted(items, key=lambda x: x.revenue, reverse=True)
        most_disc = sorted(items, key=lambda x: x.avg_discount_pct, reverse=True)

        return ReportProductsResponse(
            best_selling_by_qty=best_qty,
            best_selling_by_revenue=best_rev,
            most_discounted=most_disc,
            period=filters.get("period", "month"),
            generated_at=utc_now(),
        )

    def get_discounts_report(self, filters: Dict[str, Any]) -> ReportDiscountsResponse:
        deals = self._get_matching_deals(filters)

        # Rep groups
        rep_deals: Dict[int, List[Deal]] = defaultdict(list)
        for d in deals:
            rep_deals[d.owner_odoo_user_id].append(d)

        # User mirror lookup
        users = self.db.query(DealflowUser).all()
        user_names = {u.odoo_user_id: u.name for u in users}

        # Anomaly count per rep
        deal_ids = [d.id for d in deals]
        anomaly_counts: Dict[int, int] = defaultdict(int)
        if deal_ids:
            alerts = (
                self.db.query(DealAlert)
                .filter(
                    DealAlert.deal_id.in_(deal_ids),
                    DealAlert.type == AlertType.DISCOUNT_ANOMALY.value,
                )
                .all()
            )
            # Map deal_id to owner
            deal_owner_map = {d.id: d.owner_odoo_user_id for d in deals}
            for al in alerts:
                rep_id = deal_owner_map.get(al.deal_id)
                if rep_id:
                    anomaly_counts[rep_id] += 1

        rep_stats: List[RepDiscountPerformanceItem] = []
        all_discounts: List[Decimal] = []
        total_overages = 0

        for rep_id, d_list in rep_deals.items():
            rep_name = user_names.get(rep_id, f"Sales Rep #{rep_id}")
            team_id = d_list[0].sales_team_odoo_id if d_list else None
            deal_count = len(d_list)

            tot_rev = Decimal("0.00")
            weighted_disc_sum = Decimal("0.00")
            overage_count = 0
            max_disc = Decimal("0.00")

            for d in d_list:
                amt = d.amount_total_cache or Decimal("0.00")
                disc = d.order_discount_pct or Decimal("0.00")
                tot_rev += amt
                weighted_disc_sum += amt * disc
                all_discounts.append(disc)

                if disc > max_disc:
                    max_disc = disc

                # Baseline overage check: discount > 10% or baseline
                if disc > Decimal("10.00"):
                    overage_count += 1

            total_overages += overage_count
            avg_weighted_disc = Decimal("0.00")
            if tot_rev > Decimal("0.00"):
                avg_weighted_disc = Decimal(str(round(weighted_disc_sum / tot_rev, 2)))
            elif deal_count > 0:
                avg_weighted_disc = Decimal(str(round(sum(d.order_discount_pct for d in d_list) / deal_count, 2)))

            overage_freq = Decimal("0.00")
            if deal_count > 0:
                overage_freq = Decimal(str(round((overage_count / deal_count) * 100.0, 2)))

            rep_stats.append(
                RepDiscountPerformanceItem(
                    rep_id=rep_id,
                    rep_name=rep_name,
                    team_id=team_id,
                    deals_count=deal_count,
                    total_revenue=tot_rev,
                    avg_weighted_discount_pct=avg_weighted_disc,
                    overage_count=overage_count,
                    overage_frequency_pct=overage_freq,
                    anomaly_count=anomaly_counts.get(rep_id, 0),
                    max_discount_pct=max_disc,
                )
            )

        company_avg = Decimal("0.00")
        if all_discounts:
            company_avg = Decimal(str(round(sum(all_discounts) / len(all_discounts), 2)))

        return ReportDiscountsResponse(
            rep_stats=sorted(rep_stats, key=lambda x: x.avg_weighted_discount_pct, reverse=True),
            company_avg_discount_pct=company_avg,
            total_anomalies=sum(anomaly_counts.values()),
            total_overages=total_overages,
            period=filters.get("period", "month"),
            generated_at=utc_now(),
        )

    def get_pipeline_report(self, filters: Dict[str, Any]) -> ReportPipelineResponse:
        deals = self._get_matching_deals(filters)

        # 1. Pipeline Funnel by DealStatus
        status_groups: Dict[str, List[Deal]] = defaultdict(list)
        for d in deals:
            status_groups[d.status].append(d)

        funnel: List[PipelineStageItem] = []
        for st in DealStatus:
            d_list = status_groups.get(st.value, [])
            c = len(d_list)
            val = sum((d.amount_total_cache or Decimal("0.00")) for d in d_list)
            avg_d = Decimal("0.00")
            avg_m = Decimal("0.00")
            if c > 0:
                avg_d = Decimal(str(round(sum(d.order_discount_pct or Decimal("0.00") for d in d_list) / c, 2)))
                avg_m = Decimal(str(round(sum(d.margin_pct_cache or Decimal("0.00") for d in d_list) / c, 2)))

            funnel.append(
                PipelineStageItem(
                    status=st.value,
                    count=c,
                    total_value=val,
                    avg_discount_pct=avg_d,
                    avg_margin_pct=avg_m,
                )
            )

        # 2. Risk Distribution
        sev_groups: Dict[str, List[Deal]] = defaultdict(list)
        for d in deals:
            sev = d.current_severity or RiskSeverity.LOW.value
            sev_groups[sev].append(d)

        risk_dist: List[RiskSeverityBreakdown] = []
        for sev in RiskSeverity:
            d_list = sev_groups.get(sev.value, [])
            c = len(d_list)
            val = sum((d.amount_total_cache or Decimal("0.00")) for d in d_list)
            avg_score = Decimal("0.00")
            if c > 0:
                scores = [d.current_risk_score for d in d_list if d.current_risk_score is not None]
                if scores:
                    avg_score = Decimal(str(round(sum(scores) / len(scores), 2)))

            risk_dist.append(
                RiskSeverityBreakdown(
                    severity=sev.value,
                    count=c,
                    total_value=val,
                    avg_risk_score=avg_score,
                )
            )

        # 3. Top Risk Factors
        factor_counts: Dict[str, List[Decimal]] = defaultdict(list)
        deal_ids = [d.id for d in deals if d.current_assessment_id]
        if deal_ids:
            assessment_ids = [d.current_assessment_id for d in deals if d.current_assessment_id]
            factors = (
                self.db.query(RiskFactor)
                .filter(RiskFactor.risk_assessment_id.in_(assessment_ids))
                .all()
            )
            for f in factors:
                factor_counts[f.factor_type].append(Decimal(str(f.contribution or 0)))

        top_factors = []
        for ftype, contribs in factor_counts.items():
            avg_c = Decimal(str(round(sum(contribs) / len(contribs), 2))) if contribs else Decimal("0.00")
            top_factors.append(
                TopRiskFactorItem(
                    factor_type=ftype,
                    count=len(contribs),
                    avg_contribution=avg_c,
                )
            )
        top_factors = sorted(top_factors, key=lambda x: x.count, reverse=True)

        tot_val = sum((d.amount_total_cache or Decimal("0.00")) for d in deals)
        return ReportPipelineResponse(
            pipeline_funnel=funnel,
            risk_distribution=risk_dist,
            top_risk_factors=top_factors,
            total_pipeline_value=tot_val,
            total_deals_count=len(deals),
            period=filters.get("period", "month"),
            generated_at=utc_now(),
        )

    def get_fulfillment_report(self, filters: Dict[str, Any]) -> ReportFulfillmentResponse:
        deals = self._get_matching_deals(filters)
        deal_ids = [d.id for d in deals]

        plans: List[FulfillmentPlan] = []
        if deal_ids:
            plans = self.db.query(FulfillmentPlan).filter(FulfillmentPlan.deal_id.in_(deal_ids)).all()

        total_plans = len(plans)
        total_shipments = sum(p.estimated_shipments for p in plans)

        wh_breakdown_map: Dict[int, Dict[str, Any]] = defaultdict(
            lambda: {"shipments": 0, "allocated_qty": 0, "shipping_cost": Decimal("0.00")}
        )

        split_plans_count = 0
        backorder_plans_count = 0

        # Warehouse profiles for naming
        profiles = {p.odoo_warehouse_id: p for p in self.db.query(WarehouseProfile).all()}
        gw_wh_names: Dict[int, str] = {1: "Main Warehouse", 2: "East Warehouse", 3: "West Warehouse"}
        if self.gateway:
            try:
                for w in self.gateway.get_warehouses():
                    gw_wh_names[w.id] = w.name
            except Exception:
                pass

        for p in plans:
            wh_allocated: Set[int] = set()
            has_bo = False

            for line in p.lines:
                if line.allocated_qty > 0 and line.odoo_warehouse_id:
                    wh_allocated.add(line.odoo_warehouse_id)
                    item = wh_breakdown_map[line.odoo_warehouse_id]
                    item["allocated_qty"] += line.allocated_qty
                    item["shipping_cost"] += (line.shipping_cost or Decimal("0.00"))

                if line.backorder_qty > 0:
                    has_bo = True

            for wid in wh_allocated:
                wh_breakdown_map[wid]["shipments"] += 1

            if len(wh_allocated) >= 2:
                split_plans_count += 1
            if has_bo:
                backorder_plans_count += 1

        split_rate = Decimal("0.00")
        backorder_rate = Decimal("0.00")
        if total_plans > 0:
            split_rate = Decimal(str(round((split_plans_count / total_plans) * 100.0, 2)))
            backorder_rate = Decimal(str(round((backorder_plans_count / total_plans) * 100.0, 2)))

        # On time delivery: check delivery slippage alerts
        slippage_deals = set()
        if deal_ids:
            alerts = (
                self.db.query(DealAlert)
                .filter(
                    DealAlert.deal_id.in_(deal_ids),
                    DealAlert.type == AlertType.DELIVERY_SLIPPAGE.value,
                )
                .all()
            )
            slippage_deals = {a.deal_id for a in alerts}

        on_time_pct = Decimal("100.00")
        if deals:
            on_time_count = len(deals) - len(slippage_deals)
            on_time_pct = Decimal(str(round(max(0.0, (on_time_count / len(deals)) * 100.0), 2)))

        warehouse_items: List[WarehouseFulfillmentItem] = []
        for wid, data in wh_breakdown_map.items():
            wh_name = gw_wh_names.get(wid, f"Warehouse #{wid}")
            warehouse_items.append(
                WarehouseFulfillmentItem(
                    warehouse_id=wid,
                    warehouse_name=wh_name,
                    shipment_count=data["shipments"],
                    total_allocated_qty=data["allocated_qty"],
                    total_shipping_cost=data["shipping_cost"],
                )
            )

        return ReportFulfillmentResponse(
            warehouse_breakdown=warehouse_items,
            split_rate=split_rate,
            backorder_rate=backorder_rate,
            on_time_pct=on_time_pct,
            total_plans=total_plans,
            total_shipments=total_shipments,
            period=filters.get("period", "month"),
            generated_at=utc_now(),
        )

    def get_billing_report(self, filters: Dict[str, Any]) -> ReportBillingResponse:
        deals = self._get_matching_deals(filters)

        total_invoiced = Decimal("0.00")
        total_paid = Decimal("0.00")
        total_outstanding = Decimal("0.00")
        overdue_amt = Decimal("0.00")
        overdue_cnt = 0
        mrr = Decimal("0.00")
        active_subs = 0
        credit_notes_amt = Decimal("0.00")
        credit_notes_cnt = 0

        # Query gateway billing summary for matching deals
        if self.gateway:
            for d in deals:
                try:
                    summary = self.gateway.get_billing_summary(d.odoo_sale_order_id)
                    for inv in summary.invoices:
                        amt = Decimal(str(inv.get("amount_total", 0.0)))
                        paid = Decimal(str(inv.get("amount_paid", 0.0)))
                        residual = Decimal(str(inv.get("amount_residual", 0.0)))
                        total_invoiced += amt
                        total_paid += paid
                        total_outstanding += residual
                        if inv.get("is_overdue"):
                            overdue_amt += residual
                            overdue_cnt += 1

                    for sub in summary.subscriptions:
                        if sub.get("state") in ("in_progress", "active"):
                            mrr += Decimal(str(sub.get("recurring_total", 0.0)))
                            active_subs += 1
                except Exception:
                    pass
        else:
            # Fallback based on deal caches
            for d in deals:
                if d.status in (DealStatus.INVOICED.value, DealStatus.PAID.value):
                    total_invoiced += (d.amount_total_cache or Decimal("0.00"))
                    if d.status == DealStatus.PAID.value:
                        total_paid += (d.amount_total_cache or Decimal("0.00"))
                    else:
                        total_outstanding += (d.amount_total_cache or Decimal("0.00"))
                if d.recurring_first_cycle_total_cache and d.recurring_first_cycle_total_cache > Decimal("0.00"):
                    mrr += d.recurring_first_cycle_total_cache
                    active_subs += 1

        return ReportBillingResponse(
            total_invoiced=total_invoiced,
            total_paid=total_paid,
            total_outstanding=total_outstanding,
            overdue_amount=overdue_amt,
            overdue_count=overdue_cnt,
            mrr=mrr,
            active_subscriptions_count=active_subs,
            total_credit_notes_amount=credit_notes_amt,
            credit_notes_count=credit_notes_cnt,
            period=filters.get("period", "month"),
            generated_at=utc_now(),
        )
