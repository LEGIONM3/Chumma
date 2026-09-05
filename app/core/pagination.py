from typing import Any, Generic, List, Optional, Tuple, TypeVar
from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams:
    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number starting at 1"),
        page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
        sort: Optional[str] = Query(None, description="Field name to sort by"),
        order: str = Query("asc", pattern="^(asc|desc)$", description="Sort order: asc or desc"),
    ):
        self.page = page
        self.page_size = page_size
        self.sort = sort
        self.order = order

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def paginate_query(query: Any, params: PaginationParams) -> Tuple[List[Any], Any]:
    total = query.count()
    items = query.offset(params.offset).limit(params.limit).all()
    total_pages = (total + params.page_size - 1) // params.page_size if params.page_size > 0 else 0
    from app.schemas.common import PaginationMeta
    meta = PaginationMeta(
        page=params.page,
        page_size=params.page_size,
        total=total,
        total_pages=total_pages,
    )
    return items, meta
