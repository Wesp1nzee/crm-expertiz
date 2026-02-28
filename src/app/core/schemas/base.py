from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginationMeta(BaseModel):
    total_items: int
    total_pages: int
    current_page: int
    per_page: int
    has_next: bool
    has_prev: bool
    next_page_url: str | None = None
    prev_page_url: str | None = None


class PaginatedResponse[T](BaseModel):
    items: list[T]
    meta: PaginationMeta
