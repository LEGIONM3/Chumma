from fastapi import APIRouter
from app.api.v1.endpoints import (
    admin,
    approvals,
    auth,
    deals,
    events,
    fulfillment,
    governance,
    health,
    negotiations,
    notifications,
    policy,
    portal,
    recommendations,
    reports,
    users,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(deals.router, prefix="/deals", tags=["deals"])
api_router.include_router(governance.router, prefix="/governance", tags=["governance"])
api_router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
api_router.include_router(negotiations.router, prefix="/negotiations", tags=["negotiations"])
api_router.include_router(portal.router, prefix="/portal", tags=["portal"])
api_router.include_router(recommendations.router, tags=["recommendations"])
api_router.include_router(fulfillment.router, tags=["fulfillment"])
api_router.include_router(policy.router, tags=["policy"])
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(health.router, tags=["health", "alerts", "dashboard"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
