from typing import Any, List, Optional
from fastapi import APIRouter, Depends, Query
from app.core.deps import get_odoo_gateway
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse

router = APIRouter()


@router.get("/products", response_model=DataResponse[List[Any]])
def list_products(
    gateway: OdooGateway = Depends(get_odoo_gateway),
    category_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
):
    if hasattr(gateway, "products"):
        prods = list(gateway.products.values())
        if category_id:
            prods = [p for p in prods if p.get("category_id") == category_id]
        if search:
            s_lower = search.lower()
            prods = [p for p in prods if s_lower in p.get("name", "").lower()]
        return DataResponse(data=prods)
    return DataResponse(data=[])


@router.get("/products/{product_id}", response_model=DataResponse[Any])
def get_product(
    product_id: int,
    gateway: OdooGateway = Depends(get_odoo_gateway),
):
    if hasattr(gateway, "products") and product_id in gateway.products:
        return DataResponse(data=gateway.products[product_id])
    return DataResponse(data={"id": product_id, "name": f"Product {product_id}"})


@router.get("/categories", response_model=DataResponse[List[Any]])
def list_categories(
    gateway: OdooGateway = Depends(get_odoo_gateway),
):
    if hasattr(gateway, "categories"):
        return DataResponse(data=list(gateway.categories.values()))
    return DataResponse(data=[
        {"id": 1, "name": "All", "complete_name": "All"},
        {"id": 2, "name": "Hardware", "complete_name": "All / Hardware"},
        {"id": 3, "name": "Accessories", "complete_name": "All / Accessories"},
        {"id": 4, "name": "Services", "complete_name": "All / Services"},
    ])


@router.get("/warehouses", response_model=DataResponse[List[Any]])
def list_warehouses(
    gateway: OdooGateway = Depends(get_odoo_gateway),
):
    warehouses = gateway.get_warehouses()
    return DataResponse(data=[{"id": w.id, "name": w.name, "code": w.code} for w in warehouses])
