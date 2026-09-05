from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.deps import get_current_active_user, get_db, get_odoo_gateway
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse
from app.schemas.identity import (
    MagicLinkRequest,
    MagicLinkResponse,
    MagicLinkVerifyRequest,
    TokenResponse,
    UserLoginRequest,
    UserRead,
)
from app.services import auth_service

router = APIRouter()


@router.post("/login", response_model=DataResponse[TokenResponse])
def login(
    payload: UserLoginRequest,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
):
    token_resp = auth_service.authenticate_user(
        db=db,
        gateway=gateway,
        login=payload.login,
        password=payload.password,
    )
    return DataResponse(data=token_resp)


@router.post("/magic-link/request", response_model=DataResponse[MagicLinkResponse])
def request_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
):
    client_ip = request.client.host if request.client else None
    raw_token, portal_url, partner_id = auth_service.create_magic_link(
        db=db,
        gateway=gateway,
        email=payload.email,
        partner_id=payload.partner_id,
        deal_id=payload.deal_id,
        ip=client_ip,
    )
    return DataResponse(
        data=MagicLinkResponse(
            message=f"Magic link generated for customer partner ID {partner_id}.",
            token=raw_token,
            portal_url=portal_url,
        )
    )


@router.post("/magic-link/verify", response_model=DataResponse[TokenResponse])
def verify_magic_link(
    payload: MagicLinkVerifyRequest,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
):
    token_resp = auth_service.verify_magic_link(
        db=db,
        gateway=gateway,
        raw_token=payload.token,
    )
    return DataResponse(data=token_resp)


@router.get("/me", response_model=DataResponse[UserRead])
def get_current_user_profile(
    current_user: DealflowUser = Depends(get_current_active_user),
):
    return DataResponse(data=UserRead.model_validate(current_user))
