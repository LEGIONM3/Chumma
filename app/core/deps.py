from typing import Any, Callable, Generator, List, Optional
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.core import security
from app.core.config import settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.rbac import Role
from app.db.session import SessionLocal

security_bearer = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_odoo_gateway():
    from app.odoo import get_gateway
    return get_gateway()


def get_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    db: Session = Depends(get_db),
) -> Any:
    if not auth:
        raise UnauthorizedError("Missing authorization header.")
    try:
        payload = security.decode_token(auth.credentials, audience="internal")
    except Exception as e:
        raise UnauthorizedError(f"Invalid or expired token: {str(e)}")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Token missing user subject.")

    from app.models.identity import DealflowUser
    user = db.query(DealflowUser).filter(DealflowUser.odoo_user_id == int(user_id)).first()
    if not user:
        raise UnauthorizedError("User not found.")
    if not user.is_active:
        raise ForbiddenError("User account is inactive.")
    return user


def get_current_active_user(
    current_user: Any = Depends(get_current_user),
) -> Any:
    if not current_user.is_active:
        raise ForbiddenError("Inactive user account.")
    return current_user


def require_roles(*allowed_roles: Role) -> Callable:
    def role_checker(current_user: Any = Depends(get_current_active_user)) -> Any:
        if current_user.role == Role.ADMIN.value:
            return current_user
        if current_user.role not in [r.value for r in allowed_roles]:
            raise ForbiddenError(
                f"Operation requires one of roles: {[r.value for r in allowed_roles]}, user has role: {current_user.role}"
            )
        return current_user

    return role_checker


def get_current_portal_user(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    db: Session = Depends(get_db),
) -> Any:
    if not auth:
        raise UnauthorizedError("Missing portal authorization header.")
    try:
        payload = security.decode_token(auth.credentials, audience="portal")
    except Exception as e:
        raise UnauthorizedError(f"Invalid or expired portal token: {str(e)}")

    partner_id = payload.get("partner_id") or payload.get("customer_id")
    if not partner_id:
        raise UnauthorizedError("Portal token missing partner_id / customer_id claim.")

    from app.models.identity import DealflowUser
    user = db.query(DealflowUser).filter(DealflowUser.partner_id == int(partner_id)).first()
    if not user:
        # Create a lightweight proxy object for portal caller if no specific user record
        from types import SimpleNamespace
        return SimpleNamespace(
            odoo_user_id=0,
            login=f"partner_{partner_id}",
            name=payload.get("name", f"Partner {partner_id}"),
            role=Role.CUSTOMER.value,
            is_portal=True,
            partner_id=int(partner_id),
            is_active=True,
        )

    if not user.is_active:
        raise ForbiddenError("Customer user account is inactive.")
    return user
