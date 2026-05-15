import json
from fastapi import APIRouter, Depends
from app.auth import require_user
from app.models import SearchRequest, SearchResponse, ChunkResult
from app.services.embedder import embed_single
from app.services import store
from app.database import get_conn
from app.config import get_settings

router = APIRouter(prefix="/documents", tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search_documents(
    request: SearchRequest,
    _: str = Depends(require_user),
):
    """
    Llamado por chat-service para obtener contexto de documentos del usuario.
    user_email en el body define el scope de búsqueda.
    """
    settings = get_settings()
    query_vec = await embed_single(request.query)

    async with get_conn() as conn:
        rows = await store.search_chunks(
            query_vector=query_vec,
            user_email=request.user_email,
            top_k=request.top_k or settings.search_top_k,
            threshold=request.similarity_threshold or settings.similarity_threshold,
            conn=conn,
        )

    results = [
        ChunkResult(
            chunk_text=r["chunk_text"],
            page_number=r["page_number"],
            filename=r["filename"],
            document_id=r["document_id"],
            similarity=float(r["similarity"]),
            metadata=json.loads(r["metadata"]) if r["metadata"] else None,
        )
        for r in rows
    ]

    return SearchResponse(query=request.query, results=results, found=len(results))
