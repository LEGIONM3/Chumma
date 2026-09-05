from typing import Any, Dict, List
from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session
from app.core.deps import get_current_active_user, get_db, get_odoo_gateway, require_roles
from app.core.errors import NotFoundError
from app.jobs.tasks import list_registered_jobs, run_job
from app.models.enums import Role
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse

router = APIRouter()


@router.get("/jobs", response_model=DataResponse[List[Dict[str, Any]]])
def get_jobs_list(
    current_user: DealflowUser = Depends(get_current_active_user),
):
    jobs = list_registered_jobs()
    return DataResponse(data=jobs)


@router.post(
    "/jobs/run/{name}",
    response_model=DataResponse[Dict[str, Any]],
    dependencies=[Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER))],
)
def run_job_endpoint(
    name: str = Path(..., description="Name of the background job to execute"),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    try:
        res = run_job(job_name=name, db=db, gateway=gateway)
        return DataResponse(data=res)
    except ValueError as e:
        raise NotFoundError(str(e))


@router.get("/outbox", response_model=DataResponse[List[Dict[str, Any]]])
def get_outbox_endpoint(
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    outbox_items = getattr(gateway, "outbox", [])
    return DataResponse(data=list(outbox_items))
