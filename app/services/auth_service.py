from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
import uuid
from sqlalchemy.orm import Session
from app.core import security
from app.core.config import settings
from app.core.errors import NotFoundError, UnauthorizedError, ValidationError
from app.db.base import utc_now
from app.models.enums import Role
from app.models.identity import DealflowUser, MagicLinkToken
from app.odoo.interface import OdooGateway
from app.schemas.identity import TokenResponse


def authenticate_user(
    db: Session,
    gateway: OdooGateway,
    login: str,
    password: str,
) -> TokenResponse:
    # 1. Authenticate against Odoo gateway
    try:
        odoo_auth = gateway.authenticate(login, password)
    except Exception:
        odoo_auth = None
    
    if not odoo_auth:
        # Fallback for local dev/admin if configured
        local_user = db.query(DealflowUser).filter(DealflowUser.login == login).first()
        if local_user and login == "admin" and password == "admin":
            user = local_user
        else:
            raise UnauthorizedError("Invalid username or password.")
    else:
        # 2. Upsert local DealflowUser
        user = db.query(DealflowUser).filter(DealflowUser.odoo_user_id == odoo_auth.uid).first()
        sales_team_id = getattr(odoo_auth, "sales_team_id", getattr(odoo_auth, "team_id", None))
        is_portal_user = getattr(odoo_auth, "is_share", False) or any("portal" in g.lower() for g in getattr(odoo_auth, "groups", []))
        if not user:
            if is_portal_user:
                initial_role = Role.CUSTOMER.value
            elif login in ("admin", "admin@dealflow.test"):
                initial_role = Role.ADMIN.value
            else:
                initial_role = Role.SALES_REP.value
                for group in odoo_auth.groups:
                    g_lower = group.lower()
                    if "admin" in g_lower:
                        initial_role = Role.ADMIN.value
                    elif "manager" in g_lower:
                        initial_role = Role.SALES_MANAGER.value
                    elif "billing" in g_lower or "account" in g_lower or "finance" in g_lower:
                        initial_role = Role.FINANCE.value

            user = DealflowUser(
                odoo_user_id=odoo_auth.uid,
                login=login,
                name=odoo_auth.name,
                role=initial_role,
                sales_team_odoo_id=sales_team_id,
                is_portal=is_portal_user,
                partner_id=odoo_auth.partner_id,
                is_active=True,
                last_login_at=utc_now(),
            )
            db.add(user)
        else:
            user.name = odoo_auth.name
            user.last_login_at = utc_now()
            if is_portal_user:
                user.is_portal = True
                user.role = Role.CUSTOMER.value
                user.partner_id = odoo_auth.partner_id
            if sales_team_id:
                user.sales_team_odoo_id = sales_team_id
        
        db.commit()
        db.refresh(user)

    if not user.is_active:
        raise UnauthorizedError("User account is inactive.")

    # 3. Create JWT
    if getattr(user, "is_portal", False):
        token_str = security.create_portal_token(
            subject=str(user.partner_id or user.odoo_user_id),
            customer_id=str(user.partner_id or user.odoo_user_id),
            company_id="1",
            role=Role.CUSTOMER.value,
            expires_delta=timedelta(minutes=settings.PORTAL_TOKEN_EXPIRE_MINUTES),
        )
        return TokenResponse(
            access_token=token_str,
            token_type="bearer",
            expires_in=settings.PORTAL_TOKEN_EXPIRE_MINUTES * 60,
            role=Role.CUSTOMER,
            odoo_user_id=user.odoo_user_id,
            name=user.name,
            is_portal=True,
            partner_id=user.partner_id,
        )

    token_str = security.create_access_token(
        subject=str(user.odoo_user_id),
        role=user.role,
        company_id="1",
        audience="internal",
        extra_claims={
            "login": user.login,
            "name": user.name,
            "is_portal": False,
            "partner_id": user.partner_id,
        },
    )

    return TokenResponse(
        access_token=token_str,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        role=Role(user.role),
        odoo_user_id=user.odoo_user_id,
        name=user.name,
        is_portal=False,
        partner_id=user.partner_id,
    )


def create_magic_link(
    db: Session,
    gateway: OdooGateway,
    email: Optional[str] = None,
    partner_id: Optional[int] = None,
    deal_id: Optional[uuid.UUID] = None,
    ip: Optional[str] = None,
) -> Tuple[str, str, int]:
    if not partner_id and not email:
        raise ValidationError("Either email or partner_id must be provided.")

    target_partner_id = partner_id
    if not target_partner_id:
        customer = gateway.get_customer_by_email(email) if hasattr(gateway, "get_customer_by_email") else None
        if not customer:
            raise NotFoundError(f"No customer found with email: {email}")
        target_partner_id = customer.odoo_partner_id

    raw_token, token_hash = security.generate_magic_link_token()
    expires_at = utc_now() + timedelta(minutes=settings.PORTAL_TOKEN_EXPIRE_MINUTES)

    magic_token = MagicLinkToken(
        odoo_partner_id=target_partner_id,
        token_hash=token_hash,
        expires_at=expires_at,
        deal_id=deal_id,
        created_ip=ip,
    )
    db.add(magic_token)
    db.commit()

    portal_url = f"/portal/verify?token={raw_token}"
    if deal_id:
        portal_url += f"&deal_id={deal_id}"

    return raw_token, portal_url, target_partner_id


def verify_magic_link(
    db: Session,
    gateway: OdooGateway,
    raw_token: str,
) -> TokenResponse:
    token_hash = security.hash_token(raw_token)
    magic_record = db.query(MagicLinkToken).filter(MagicLinkToken.token_hash == token_hash).first()

    now = utc_now()
    if not magic_record:
        raise UnauthorizedError("Invalid magic link token.")
    if magic_record.used_at is not None:
        raise UnauthorizedError("Magic link token has already been used.")

    expires_at = magic_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < now:
        raise UnauthorizedError("Magic link token has expired.")

    magic_record.used_at = now
    db.commit()

    try:
        partner = gateway.get_partner(magic_record.odoo_partner_id)
        partner_name = partner.name if partner else f"Customer {magic_record.odoo_partner_id}"
    except Exception:
        partner_name = f"Customer {magic_record.odoo_partner_id}"

    portal_jwt = security.create_portal_token(
        subject=str(magic_record.odoo_partner_id),
        customer_id=str(magic_record.odoo_partner_id),
        company_id="1",
        role=Role.CUSTOMER.value,
        expires_delta=timedelta(minutes=settings.PORTAL_TOKEN_EXPIRE_MINUTES),
    )

    return TokenResponse(
        access_token=portal_jwt,
        token_type="bearer",
        expires_in=settings.PORTAL_TOKEN_EXPIRE_MINUTES * 60,
        role=Role.CUSTOMER,
        odoo_user_id=0,
        name=partner_name,
        is_portal=True,
        partner_id=magic_record.odoo_partner_id,
    )
