import asyncpg
import json
from app.services.chunker import Chunk


async def document_exists(user_email: str, content_hash: str, conn: asyncpg.Connection) -> int | None:
    row = await conn.fetchrow(
        "SELECT id FROM documents WHERE user_email = $1 AND content_hash = $2 AND status != 'deleted'",
        user_email, content_hash,
    )
    return row["id"] if row else None


async def insert_document(
    user_email: str,
    filename: str,
    file_type: str,
    content_hash: str,
    page_count: int,
    summary: str,
    conn: asyncpg.Connection,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO documents
            (user_email, filename, file_type, upload_date, content_hash,
             page_count, content, source, status)
        VALUES ($1, $2, $3, NOW(), $4, $5, $6, $7, 'ready')
        RETURNING id
        """,
        user_email, filename, file_type, content_hash,
        page_count, summary, filename,
    )
    return row["id"]


async def insert_chunks(
    document_id: int,
    chunks: list[Chunk],
    embeddings: list[list[float]],
    conn: asyncpg.Connection,
) -> None:
    records = [
        (
            document_id,
            chunk.text,
            chunk.chunk_index,
            "[" + ",".join(str(v) for v in emb) + "]",
            json.dumps({
                "section_title": chunk.section_title,
                "exercise_ref": chunk.exercise_ref,
            }),
            chunk.page_number,
        )
        for chunk, emb in zip(chunks, embeddings)
    ]
    await conn.executemany(
        """
        INSERT INTO document_embeddings
            (document_id, chunk_text, chunk_index, embedding, metadata, page_number)
        VALUES ($1, $2, $3, CAST($4 AS vector), $5::jsonb, $6)
        """,
        records,
    )


async def search_chunks(
    query_vector: list[float],
    user_email: str,
    top_k: int,
    threshold: float,
    conn: asyncpg.Connection,
    preferred_document_id: int | None = None,
) -> list[dict]:
    vec = "[" + ",".join(str(v) for v in query_vector) + "]"
    rows = await conn.fetch(
        """
        SELECT
            de.chunk_text,
            de.chunk_index,
            de.page_number,
            de.metadata,
            d.filename,
            d.id AS document_id,
            1 - (de.embedding <=> CAST($1 AS vector)) AS similarity
        FROM document_embeddings de
        JOIN documents d ON d.id = de.document_id
        WHERE d.user_email = $2
          AND d.status = 'ready'
          AND 1 - (de.embedding <=> CAST($1 AS vector)) >= $3
          AND ($5::int IS NULL OR d.id = $5)
        ORDER BY
            de.embedding <=> CAST($1 AS vector)
        LIMIT $4
        """,
        vec, user_email, threshold, top_k, preferred_document_id,
    )
    return [dict(r) for r in rows]


async def search_chunks_by_exercise(
    exercise_num: str,
    user_email: str,
    conn: asyncpg.Connection,
    preferred_document_id: int | None = None,
) -> list[dict]:
    """Busca chunks por exercise_ref. Cuando preferred_document_id está seteado
    el filtro es OBLIGATORIO — nunca se hace fallback a otros documentos."""
    params: list = [user_email, f"%{exercise_num}%"]
    doc_filter = ""
    if preferred_document_id is not None:
        params.append(preferred_document_id)
        doc_filter = f"AND d.id = ${len(params)}"

    rows = await conn.fetch(
        f"""
        SELECT
            de.chunk_text,
            de.chunk_index,
            de.page_number,
            de.metadata,
            d.filename,
            d.id AS document_id,
            1.0 AS similarity
        FROM document_embeddings de
        JOIN documents d ON d.id = de.document_id
        WHERE d.user_email = $1
          AND d.status = 'ready'
          AND de.metadata->>'exercise_ref' ILIKE $2
          {doc_filter}
        ORDER BY de.chunk_index
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_user_documents(user_email: str, conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT
            d.id, d.filename, d.file_type, d.upload_date, d.page_count,
            COUNT(de.id) AS chunk_count
        FROM documents d
        LEFT JOIN document_embeddings de ON de.document_id = d.id
        WHERE d.user_email = $1 AND d.status != 'deleted'
        GROUP BY d.id
        ORDER BY d.upload_date DESC
        """,
        user_email,
    )
    return [dict(r) for r in rows]


async def delete_document(doc_id: int, user_email: str, conn: asyncpg.Connection) -> bool:
    result = await conn.execute(
        "UPDATE documents SET status = 'deleted' WHERE id = $1 AND user_email = $2",
        doc_id, user_email,
    )
    return result == "UPDATE 1"
