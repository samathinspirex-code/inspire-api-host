from pydantic import BaseModel


class Pagination(BaseModel):
    page: int
    size: int
    total_items: int
    total_pages: int
