from pydantic import BaseModel, Field
from datetime import datetime
from typing import Any


class DocumentOut(BaseModel):
    id: int
    filename: str
    file_type: str
    upload_date: datetime
    page_count: int | None
    chunk_count: int
    download_available: bool = True


class AdminDocumentSummary(BaseModel):
    id: int
    filename: str
    file_type: str
    upload_date: datetime
    page_count: int | None
    chunk_count: int
    owner_email: str
    query_count: int
    unique_users: int
    last_used_at: datetime | None = None


class AdminDocumentPage(BaseModel):
    content: list[AdminDocumentSummary]
    total_elements: int
    total_pages: int
    page: int
    size: int


class AdminDocumentMetrics(BaseModel):
    total_documents: int
    documents_used_today: int
    unique_users_today: int
    uploads_today: int


class AdminDocumentDetail(BaseModel):
    id: int
    filename: str
    file_type: str
    upload_date: datetime
    page_count: int | None
    chunk_count: int
    owner_email: str
    query_count: int
    unique_users: int
    last_used_at: datetime | None = None
    download_available: bool = True


class UploadResult(BaseModel):
    document_id: int | None = None
    filename: str
    chunk_count: int = 0
    page_count: int | None = None
    file_type: str | None = None
    status: str
    message: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=1000)
    user_email: str
    top_k: int = Field(default=8, ge=1, le=20)
    similarity_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    preferred_document_id: int | None = None


class ChunkResult(BaseModel):
    chunk_text: str
    page_number: int | None
    filename: str
    document_id: int
    similarity: float
    metadata: dict[str, Any] | None


class SearchResponse(BaseModel):
    query: str
    results: list[ChunkResult]
    found: int
    ambiguous: bool = False
    ambiguous_documents: list[str] = []
    exercise_ref: str | None = None
