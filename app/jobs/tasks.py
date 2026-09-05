from typing import Any, Dict, List
from sqlalchemy.orm import Session
from app.odoo.interface import OdooGateway
from app.services import health_service, recommendation_service

REGISTERED_JOBS: List[Dict[str, Any]] = [
    {
        "name": "detect_stalled_deals",
        "schedule": "daily 02:00",
        "description": "Scans active deals without activity beyond threshold and upserts stalled alerts.",
    },
    {
        "name": "detect_discount_anomalies",
        "schedule": "daily 02:10",
        "description": "Refreshes rep discount baseline stats and scans deals for discount anomalies.",
    },
    {
        "name": "detect_slippage",
        "schedule": "daily 02:20",
        "description": "Detects warehouse picking delays or delivery breaches on confirmed deals.",
    },
    {
        "name": "mine_upsell_pairings",
        "schedule": "weekly Sun 04:00",
        "description": "Mines historical co-purchase product affinities to discover accretive recommendation rules.",
    },
]


def list_registered_jobs() -> List[Dict[str, Any]]:
    return REGISTERED_JOBS


def run_job(job_name: str, db: Session, gateway: OdooGateway) -> Dict[str, Any]:
    if job_name == "detect_stalled_deals":
        alerts = health_service.detect_stalled_deals(db=db)
        return {"job": job_name, "status": "completed", "alerts_affected": len(alerts)}

    elif job_name == "detect_discount_anomalies":
        # Scans all active deals and recomputes stats
        res = health_service.recompute_all_alerts(db=db, gateway=gateway)
        return {"job": job_name, "status": "completed", "details": res.model_dump()}

    elif job_name == "detect_slippage":
        alerts = health_service.detect_delivery_slippages(db=db, gateway=gateway)
        return {"job": job_name, "status": "completed", "alerts_affected": len(alerts)}

    elif job_name == "mine_upsell_pairings":
        created = recommendation_service.mine_copurchase_pairings(db=db, gateway=gateway)
        return {"job": job_name, "status": "completed", "rules_generated": len(created)}

    else:
        raise ValueError(f"Unknown background job: '{job_name}'")
