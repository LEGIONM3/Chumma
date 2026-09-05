from enum import Enum
from typing import List, Optional


class Role(str, Enum):
    ADMIN = "ADMIN"
    SALES_REP = "SALES_REP"
    SALES_MANAGER = "SALES_MANAGER"
    FINANCE = "FINANCE"
    CUSTOMER = "CUSTOMER"


def check_role_permission(user_role: str, allowed_roles: List[Role]) -> bool:
    if Role.ADMIN.value == user_role:
        return True
    return user_role in [r.value for r in allowed_roles]
