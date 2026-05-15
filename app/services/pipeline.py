import hashlib
import logging
from app.services.detector import detect_file_kind
from app.services.extractor import extract
from app.services.chunker import chunk_blocks
from app.services.embedder import embed_texts
from app.services import store
from app.database import get_conn

logger = logging.getLogger(__name__)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def ingest_document(user_email: str, filename: str, data: bytes) -> dict:
    content_hash = _sha256(data)

    async with get_conn() as conn:
        existing = await store.document_exists(user_email, content_hash, conn)
        if existing:
            logger.info("Documento duplicado user=%s doc_id=%s", user_email, existing)
            return {"document_id": existing, "chunk_count": 0, "status": "duplicate"}

        try:
            kind = detect_file_kind(filename, data)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        blocks = extract(kind, data)
        if not blocks:
            return {"status": "error", "message": "No se pudo extraer texto del archivo."}

        page_count = max(b.page_number for b in blocks)
        summary = blocks[0].text[:120] + "…" if blocks else filename

        chunks = chunk_blocks(blocks)
        if not chunks:
            return {"status": "error", "message": "El documento no produjo chunks después de la extracción."}

        embeddings = await embed_texts([c.text for c in chunks])

        async with conn.transaction():
            doc_id = await store.insert_document(
                user_email=user_email,
                filename=filename,
                file_type=kind.value,
                content_hash=content_hash,
                page_count=page_count,
                summary=summary,
                conn=conn,
            )
            await store.insert_chunks(doc_id, chunks, embeddings, conn)

    logger.info("Ingesta OK doc_id=%s user=%s filename=%s chunks=%d", doc_id, user_email, filename, len(chunks))
    return {
        "document_id": doc_id,
        "filename": filename,
        "chunk_count": len(chunks),
        "page_count": page_count,
        "file_type": kind.value,
        "status": "ready",
    }
