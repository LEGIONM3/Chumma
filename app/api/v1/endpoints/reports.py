from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.deps import get_current_active_user, get_db, get_odoo_gateway, require_roles
from app.core.rbac import Role
from app.odoo.interface import OdooGateway
from app.reports.pdf_renderer import render_report_to_pdf
from app.reports.xlsx_renderer import render_report_to_xlsx
from app.schemas.common import DataResponse
from app.schemas.reports import (
    ReportApprovalsResponse,
    ReportBillingResponse,
    ReportDealsResponse,
    ReportDiscountsResponse,
    ReportFulfillmentResponse,
    ReportPipelineResponse,
    ReportProductsResponse,
    ReportSummaryResponse,
)
from app.services.report_service import ReportService

router = APIRouter()


def _render_or_json(
    report_type: str,
    data: Any,
    filters: dict,
    fmt: str,
):
    if fmt == "pdf":
        raw_dict = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        pdf_bytes = render_report_to_pdf(report_type, raw_dict, filters)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="dealflow_{report_type}_{filters.get("period", "report")}.pdf"'
            },
        )
    elif fmt == "xlsx":
        raw_dict = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        xlsx_bytes = render_report_to_xlsx(report_type, raw_dict, filters)
        return Response(
            content=xlsx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="dealflow_{report_type}_{filters.get("period", "report")}.xlsx"'
            },
        )
    return DataResponse(data=data)


@router.get("/summary", response_model=DataResponse[ReportSummaryResponse])
def get_summary_report(
    period: Optional[str] = Query("month", description="today|week|month|quarter|custom"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    team_id: Optional[int] = Query(None),
    rep_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: Any = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER, Role.SALES_REP, Role.FINANCE)),
):
    service = ReportService(db, gateway)
    filters = service.parse_filters(
        period=period,
        from_date=from_date,
        to_date=to_date,
        team_id=team_id,
        rep_id=rep_id,
        approval_status=approval_status,
        product_id=product_id,
        category_id=category_id,
        fmt=format,
    )
    result = service.get_summary_report(filters)
    return _render_or_json("summary", result, filters, format)


@router.get("/quotations", response_model=DataResponse[ReportDealsResponse])
@router.get("/deals", response_model=DataResponse[ReportDealsResponse])
def get_deals_report(
    period: Optional[str] = Query("month", description="today|week|month|quarter|custom"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    team_id: Optional[int] = Query(None),
    rep_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: Any = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER, Role.SALES_REP, Role.FINANCE)),
):
    service = ReportService(db, gateway)
    filters = service.parse_filters(
        period=period,
        from_date=from_date,
        to_date=to_date,
        team_id=team_id,
        rep_id=rep_id,
        approval_status=approval_status,
        product_id=product_id,
        category_id=category_id,
        fmt=format,
    )
    result = service.get_deals_report(filters)
    return _render_or_json("deals", result, filters, format)


@router.get("/approvals", response_model=DataResponse[ReportApprovalsResponse])
def get_approvals_report(
    period: Optional[str] = Query("month", description="today|week|month|quarter|custom"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    team_id: Optional[int] = Query(None),
    rep_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: Any = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER, Role.SALES_REP, Role.FINANCE)),
):
    service = ReportService(db, gateway)
    filters = service.parse_filters(
        period=period,
        from_date=from_date,
        to_date=to_date,
        team_id=team_id,
        rep_id=rep_id,
        approval_status=approval_status,
        product_id=product_id,
        category_id=category_id,
        fmt=format,
    )
    result = service.get_approvals_report(filters)
    return _render_or_json("approvals", result, filters, format)


@router.get("/products", response_model=DataResponse[ReportProductsResponse])
def get_products_report(
    period: Optional[str] = Query("month", description="today|week|month|quarter|custom"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    team_id: Optional[int] = Query(None),
    rep_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: Any = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER, Role.SALES_REP, Role.FINANCE)),
):
    service = ReportService(db, gateway)
    filters = service.parse_filters(
        period=period,
        from_date=from_date,
        to_date=to_date,
        team_id=team_id,
        rep_id=rep_id,
        approval_status=approval_status,
        product_id=product_id,
        category_id=category_id,
        fmt=format,
    )
    result = service.get_products_report(filters)
    return _render_or_json("products", result, filters, format)


@router.get("/discounts", response_model=DataResponse[ReportDiscountsResponse])
def get_discounts_report(
    period: Optional[str] = Query("month", description="today|week|month|quarter|custom"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    team_id: Optional[int] = Query(None),
    rep_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: Any = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER, Role.SALES_REP, Role.FINANCE)),
):
    service = ReportService(db, gateway)
    filters = service.parse_filters(
        period=period,
        from_date=from_date,
        to_date=to_date,
        team_id=team_id,
        rep_id=rep_id,
        approval_status=approval_status,
        product_id=product_id,
        category_id=category_id,
        fmt=format,
    )
    result = service.get_discounts_report(filters)
    return _render_or_json("discounts", result, filters, format)


@router.get("/pipeline", response_model=DataResponse[ReportPipelineResponse])
@router.get("/risk", response_model=DataResponse[ReportPipelineResponse])
def get_pipeline_report(
    period: Optional[str] = Query("month", description="today|week|month|quarter|custom"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    team_id: Optional[int] = Query(None),
    rep_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: Any = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER, Role.SALES_REP, Role.FINANCE)),
):
    service = ReportService(db, gateway)
    filters = service.parse_filters(
        period=period,
        from_date=from_date,
        to_date=to_date,
        team_id=team_id,
        rep_id=rep_id,
        approval_status=approval_status,
        product_id=product_id,
        category_id=category_id,
        fmt=format,
    )
    result = service.get_pipeline_report(filters)
    return _render_or_json("pipeline", result, filters, format)


@router.get("/fulfillment", response_model=DataResponse[ReportFulfillmentResponse])
def get_fulfillment_report(
    period: Optional[str] = Query("month", description="today|week|month|quarter|custom"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    team_id: Optional[int] = Query(None),
    rep_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: Any = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER, Role.SALES_REP, Role.FINANCE)),
):
    service = ReportService(db, gateway)
    filters = service.parse_filters(
        period=period,
        from_date=from_date,
        to_date=to_date,
        team_id=team_id,
        rep_id=rep_id,
        approval_status=approval_status,
        product_id=product_id,
        category_id=category_id,
        fmt=format,
    )
    result = service.get_fulfillment_report(filters)
    return _render_or_json("fulfillment", result, filters, format)


@router.get("/billing", response_model=DataResponse[ReportBillingResponse])
def get_billing_report(
    period: Optional[str] = Query("month", description="today|week|month|quarter|custom"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    team_id: Optional[int] = Query(None),
    rep_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    format: str = Query("json", pattern="^(json|pdf|xlsx)$"),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: Any = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER, Role.SALES_REP, Role.FINANCE)),
):
    service = ReportService(db, gateway)
    filters = service.parse_filters(
        period=period,
        from_date=from_date,
        to_date=to_date,
        team_id=team_id,
        rep_id=rep_id,
        approval_status=approval_status,
        product_id=product_id,
        category_id=category_id,
        fmt=format,
    )
    result = service.get_billing_report(filters)
    return _render_or_json("billing", result, filters, format)
