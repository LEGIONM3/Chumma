from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_active_user, get_db, get_odoo_gateway, require_roles
from app.core.pagination import PaginationParams
from app.core.rbac import Role
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse, MessageResponse, PaginatedResponse
from app.schemas.deal import DealDetailRead
from app.schemas.recommendation import (
    RecommendationActionRequest,
    RecommendationRead,
    RecommendationRuleCreate,
    RecommendationRuleRead,
    RecommendationRuleUpdate,
)
from app.services import recommendation_service

router = APIRouter()


@router.get("/deals/{deal_id}/recommendations", response_model=DataResponse[List[RecommendationRead]])
def list_deal_recommendations_endpoint(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    recs = recommendation_service.list_deal_recommendations(db=db, deal_id=deal_id)
    return DataResponse(data=[RecommendationRead.model_validate(r) for r in recs])


@router.post("/deals/{deal_id}/recommendations/{recommendation_id}/add", response_model=DataResponse[Dict[str, Any]])
def add_recommendation_to_deal_endpoint(
    deal_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    payload: Optional[RecommendationActionRequest] = None,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    qty = payload.quantity if payload and payload.quantity else 1.0
    result = recommendation_service.apply_recommendation_action(
        db=db,
        gateway=gateway,
        deal_id=deal_id,
        recommendation_id=recommendation_id,
        actor=current_user,
        action="ADD",
        quantity=qty,
    )
    deal_obj = result["deal"]
    lines_list = result["lines"]
    return DataResponse(
        data={
            "deal": DealDetailRead.model_validate(deal_obj).model_dump(),
            "lines": [l.model_dump() for l in lines_list],
            "recommendations": [RecommendationRead.model_validate(r).model_dump() for r in result["recommendations"]],
            "next_best_actions": result["next_best_actions"],
            "evaluation": result["evaluation"].model_dump() if hasattr(result["evaluation"], "model_dump") else str(result["evaluation"]),
        }
    )


@router.post("/deals/{deal_id}/recommendations/{recommendation_id}/dismiss", response_model=DataResponse[RecommendationRead])
def dismiss_recommendation_endpoint(
    deal_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    res = recommendation_service.apply_recommendation_action(
        db=db,
        gateway=gateway,
        deal_id=deal_id,
        recommendation_id=recommendation_id,
        actor=current_user,
        action="DISMISS",
    )
    return DataResponse(data=RecommendationRead.model_validate(res["recommendation"]))


@router.get("/recommendation-rules", response_model=PaginatedResponse[RecommendationRuleRead])
def list_recommendation_rules_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    company_id: int = Query(1),
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    pagination = PaginationParams(page=page, page_size=page_size)
    rules, meta = recommendation_service.list_rules(db=db, company_id=company_id, pagination=pagination)
    return PaginatedResponse(
        data=[RecommendationRuleRead.model_validate(r) for r in rules],
        meta=meta,
    )


@router.post("/recommendation-rules", response_model=DataResponse[RecommendationRuleRead])
def create_recommendation_rule_endpoint(
    payload: RecommendationRuleCreate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER)),
):
    rule = recommendation_service.create_rule(db=db, payload=payload)
    return DataResponse(data=RecommendationRuleRead.model_validate(rule))


@router.post("/recommendation-rules/mine", response_model=DataResponse[Dict[str, Any]])
def trigger_mining_job_endpoint(
    company_id: int = Query(1),
    lookback_days: int = Query(365),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN)),
):
    result = recommendation_service.mine_co_purchase_rules(
        db=db,
        gateway=gateway,
        company_id=company_id,
        lookback_days=lookback_days,
    )
    return DataResponse(data=result)


@router.get("/recommendation-rules/{rule_id}", response_model=DataResponse[RecommendationRuleRead])
def get_recommendation_rule_endpoint(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    rule = recommendation_service.get_rule(db=db, rule_id=rule_id)
    return DataResponse(data=RecommendationRuleRead.model_validate(rule))


@router.patch("/recommendation-rules/{rule_id}", response_model=DataResponse[RecommendationRuleRead])
def update_recommendation_rule_endpoint(
    rule_id: uuid.UUID,
    payload: RecommendationRuleUpdate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER)),
):
    rule = recommendation_service.update_rule(db=db, rule_id=rule_id, payload=payload)
    return DataResponse(data=RecommendationRuleRead.model_validate(rule))


@router.delete("/recommendation-rules/{rule_id}", response_model=MessageResponse)
def delete_recommendation_rule_endpoint(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN)),
):
    recommendation_service.delete_rule(db=db, rule_id=rule_id)
    return MessageResponse(message=f"Recommendation rule {rule_id} deleted successfully.")
