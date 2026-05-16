import json
import logging
import re
from fastapi import APIRouter, Depends
from app.auth import require_user
from app.models import SearchRequest, SearchResponse, ChunkResult
from app.services.embedder import embed_single
from app.services import store
from app.database import get_conn
from app.config import get_settings

router = APIRouter(prefix="/documents", tags=["search"])
log = logging.getLogger(__name__)

_EXERCISE_RE = re.compile(
    r"(?:ejercicio|problema|pregunta|práctico|practico|ej\.?)\s*(\d+[\.\d]*)",
    re.IGNORECASE,
)

_WORD_TO_NUM: dict[str, str] = {
    "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
    "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10",
    "once": "11", "doce": "12", "trece": "13", "catorce": "14", "quince": "15",
}
_WORD_NUM_RE = re.compile(r"\b(" + "|".join(_WORD_TO_NUM) + r")\b", re.IGNORECASE)


def _normalize_query(text: str) -> str:
    return _WORD_NUM_RE.sub(lambda m: _WORD_TO_NUM[m.group(1).lower()], text)


@router.post("/search", response_model=SearchResponse)
async def search_documents(
    request: SearchRequest,
    _: str = Depends(require_user),
):
    settings = get_settings()
    top_k = request.top_k or settings.search_top_k
    threshold = request.similarity_threshold or settings.similarity_threshold

    normalized_query = _normalize_query(request.query)
    exercise_match = _EXERCISE_RE.search(normalized_query)

    async with get_conn() as conn:
        exact_rows: list[dict] = []
        if exercise_match:
            exercise_num = exercise_match.group(1)
            exact_rows = await store.search_chunks_by_exercise(
                exercise_num=exercise_num,
                user_email=request.user_email,
                conn=conn,
                preferred_document_id=request.preferred_document_id,
            )
            log.info("[RAG] exact match '%s': %d chunks", exercise_num, len(exact_rows))

            if exact_rows and not request.preferred_document_id:
                doc_names = list(dict.fromkeys(r["filename"] for r in exact_rows))
                if len(doc_names) > 1:
                    log.info("[RAG] ambiguous: '%s' found in %d docs", exercise_num, len(doc_names))
                    return SearchResponse(
                        query=request.query,
                        results=[],
                        found=0,
                        ambiguous=True,
                        ambiguous_documents=doc_names,
                        exercise_ref=exercise_match.group(0),
                    )

        query_vec = await embed_single(request.query)
        vec_rows = await store.search_chunks(
            query_vector=query_vec,
            user_email=request.user_email,
            top_k=top_k,
            threshold=threshold,
            conn=conn,
            preferred_document_id=request.preferred_document_id,
        )

        log.info("[RAG] vector search: %d chunks (threshold=%.2f)", len(vec_rows), threshold)
        for r in vec_rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            log.info(
                "[RAG] score=%.3f exercise_ref=%s file=%s text=%s",
                float(r["similarity"]),
                meta.get("exercise_ref"),
                r["filename"],
                r["chunk_text"][:120],
            )

    # Exact matches first, then vector — deduplicate by (document_id, chunk_index)
    seen: set[tuple] = set()
    merged: list[dict] = []
    for r in exact_rows + vec_rows:
        key = (r["document_id"], r["chunk_index"])
        if key not in seen:
            seen.add(key)
            merged.append(r)

    merged = merged[:top_k]

    results = [
        ChunkResult(
            chunk_text=r["chunk_text"],
            page_number=r["page_number"],
            filename=r["filename"],
            document_id=r["document_id"],
            similarity=float(r["similarity"]),
            metadata=json.loads(r["metadata"]) if r["metadata"] else None,
        )
        for r in merged
    ]

    log.info("[RAG] returning %d chunks (exact=%d vec=%d)", len(results), len(exact_rows), len(vec_rows))
    return SearchResponse(query=request.query, results=results, found=len(results))
