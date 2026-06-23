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
    content_data: bytes | None,
    conn: asyncpg.Connection,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO documents
            (user_email, filename, file_type, upload_date, content_hash,
             page_count, content, source, status, content_data)
        VALUES ($1, $2, $3, NOW(), $4, $5, $6, $7, 'ready', $8)
        RETURNING id
        """,
        user_email, filename, file_type, content_hash,
        page_count, summary, filename, content_data,
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
                "bbox": chunk.bbox,
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
            COUNT(de.id) AS chunk_count,
            d.content_data IS NOT NULL AS download_available
        FROM documents d
        LEFT JOIN document_embeddings de ON de.document_id = d.id
        WHERE d.user_email = $1 AND d.status != 'deleted'
        GROUP BY d.id
        ORDER BY d.upload_date DESC
        """,
        user_email,
    )
    return [dict(r) for r in rows]


async def get_document_data(doc_id: int, user_email: str, conn: asyncpg.Connection) -> bytes | None:
    row = await conn.fetchrow(
        "SELECT content_data FROM documents WHERE id = $1 AND user_email = $2 AND status != 'deleted'",
        doc_id, user_email,
    )
    return row["content_data"] if row else None


async def get_document_exercises(doc_id: int, user_email: str, conn: asyncpg.Connection) -> list[dict]:
    """Retorna ejercicios únicos detectados en los chunks del documento,
    incluyendo bbox si está disponible."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT
            de.metadata->>'exercise_ref' AS exercise_ref,
            de.page_number,
            de.metadata->>'section_title' AS section_title,
            de.metadata->'bbox' AS bbox
        FROM document_embeddings de
        JOIN documents d ON d.id = de.document_id
        WHERE d.id = $1 AND d.user_email = $2 AND d.status != 'deleted'
          AND de.metadata->>'exercise_ref' IS NOT NULL
          AND de.metadata->>'exercise_ref' != ''
        ORDER BY de.page_number
        """,
        doc_id, user_email,
    )
    return [dict(r) for r in rows]


async def get_documents_needing_bbox(user_email: str, conn: asyncpg.Connection) -> list[dict]:
    """Docs that have content_data + exercise_ref in chunks, but missing bbox."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT d.id, d.filename, d.file_type
        FROM documents d
        JOIN document_embeddings de ON de.document_id = d.id
        WHERE d.user_email = $1
          AND d.status != 'deleted'
          AND d.content_data IS NOT NULL
          AND d.file_type IN ('pdf_text', 'pdf_scanned')
          AND de.metadata->>'exercise_ref' IS NOT NULL
          AND (de.metadata->'bbox' IS NULL OR de.metadata->'bbox' = 'null'::jsonb)
        ORDER BY d.id
        """,
        user_email,
    )
    return [dict(r) for r in rows]


async def get_chunks_needing_bbox(doc_id: int, conn: asyncpg.Connection) -> list[dict]:
    """Chunks with exercise_ref but no bbox for a given document."""
    rows = await conn.fetch(
        """
        SELECT de.id, de.metadata, de.page_number
        FROM document_embeddings de
        WHERE de.document_id = $1
          AND de.metadata->>'exercise_ref' IS NOT NULL
          AND (de.metadata->'bbox' IS NULL OR de.metadata->'bbox' = 'null'::jsonb)
        ORDER BY de.page_number, de.chunk_index
        """,
        doc_id,
    )
    return [dict(r) for r in rows]


async def update_chunk_metadata(chunk_id: int, metadata_update: dict, conn: asyncpg.Connection) -> None:
    """Mergea un dict en el metadata JSONB del chunk."""
    await conn.execute(
        "UPDATE document_embeddings SET metadata = metadata || $1::jsonb WHERE id = $2",
        json.dumps(metadata_update),
        chunk_id,
    )


async def delete_document(doc_id: int, user_email: str, conn: asyncpg.Connection) -> bool:
    result = await conn.execute(
        "UPDATE documents SET status = 'deleted' WHERE id = $1 AND user_email = $2",
        doc_id, user_email,
    )
    return result == "UPDATE 1"


async def record_document_usage(
    document_id: int,
    user_email: str,
    action: str,
    conn: asyncpg.Connection,
) -> None:
    await conn.execute(
        """
        INSERT INTO document_usage_events (document_id, user_email, action)
        VALUES ($1, $2, $3)
        """,
        document_id,
        user_email,
        action,
    )


async def get_admin_documents(
    conn: asyncpg.Connection,
    page: int,
    size: int,
    owner_email: str | None = None,
    filename: str | None = None,
) -> dict:
    offset = max(page, 0) * size
    email_param = f"%{owner_email.strip().lower()}%" if owner_email and owner_email.strip() else None
    filename_param = f"%{filename.strip().lower()}%" if filename and filename.strip() else None

    base_where = """
        WHERE d.status != 'deleted'
          AND ($1::text IS NULL OR LOWER(d.user_email) LIKE $1)
          AND ($2::text IS NULL OR LOWER(d.filename) LIKE $2)
    """

    rows = await conn.fetch(
        f"""
        SELECT
            d.id,
            d.filename,
            d.file_type,
            d.upload_date,
            d.page_count,
            d.user_email AS owner_email,
            COUNT(DISTINCT de.id) AS chunk_count,
            COUNT(ue.id) FILTER (WHERE ue.action IN ('SEARCH', 'VIEW')) AS query_count,
            COUNT(DISTINCT ue.user_email) FILTER (WHERE ue.action IN ('SEARCH', 'VIEW')) AS unique_users,
            MAX(ue.created_at) FILTER (WHERE ue.action IN ('SEARCH', 'VIEW')) AS last_used_at
        FROM documents d
        LEFT JOIN document_embeddings de ON de.document_id = d.id
        LEFT JOIN document_usage_events ue ON ue.document_id = d.id
        {base_where}
        GROUP BY d.id
        ORDER BY COALESCE(MAX(ue.created_at), d.upload_date) DESC, d.id DESC
        LIMIT $3 OFFSET $4
        """,
        email_param,
        filename_param,
        size,
        offset,
    )

    total = await conn.fetchval(
        f"""
        SELECT COUNT(*)
        FROM documents d
        {base_where}
        """,
        email_param,
        filename_param,
    )
    return {"content": [dict(r) for r in rows], "total": total or 0}


async def get_admin_document_metrics(
    conn: asyncpg.Connection,
    owner_email: str | None = None,
    filename: str | None = None,
) -> dict:
    email_param = f"%{owner_email.strip().lower()}%" if owner_email and owner_email.strip() else None
    filename_param = f"%{filename.strip().lower()}%" if filename and filename.strip() else None

    base_where = """
        WHERE d.status != 'deleted'
          AND ($1::text IS NULL OR LOWER(d.user_email) LIKE $1)
          AND ($2::text IS NULL OR LOWER(d.filename) LIKE $2)
    """

    total_documents = await conn.fetchval(
        f"SELECT COUNT(*) FROM documents d {base_where}",
        email_param,
        filename_param,
    )
    uploads_today = await conn.fetchval(
        f"SELECT COUNT(*) FROM documents d {base_where} AND d.upload_date >= CURRENT_DATE",
        email_param,
        filename_param,
    )
    documents_used_today = await conn.fetchval(
        f"""
        SELECT COUNT(DISTINCT d.id)
        FROM documents d
        JOIN document_usage_events ue ON ue.document_id = d.id
        {base_where}
          AND ue.created_at >= CURRENT_DATE
          AND ue.action IN ('SEARCH', 'VIEW')
        """,
        email_param,
        filename_param,
    )
    unique_users_today = await conn.fetchval(
        f"""
        SELECT COUNT(DISTINCT ue.user_email)
        FROM documents d
        JOIN document_usage_events ue ON ue.document_id = d.id
        {base_where}
          AND ue.created_at >= CURRENT_DATE
          AND ue.action IN ('SEARCH', 'VIEW')
        """,
        email_param,
        filename_param,
    )
    return {
        "total_documents": total_documents or 0,
        "documents_used_today": documents_used_today or 0,
        "unique_users_today": unique_users_today or 0,
        "uploads_today": uploads_today or 0,
    }


async def get_admin_document_detail(conn: asyncpg.Connection, doc_id: int) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT
            d.id,
            d.filename,
            d.file_type,
            d.upload_date,
            d.page_count,
            d.user_email AS owner_email,
            COUNT(DISTINCT de.id) AS chunk_count,
            COUNT(ue.id) FILTER (WHERE ue.action IN ('SEARCH', 'VIEW')) AS query_count,
            COUNT(DISTINCT ue.user_email) FILTER (WHERE ue.action IN ('SEARCH', 'VIEW')) AS unique_users,
            MAX(ue.created_at) FILTER (WHERE ue.action IN ('SEARCH', 'VIEW')) AS last_used_at,
            d.content_data IS NOT NULL AS download_available
        FROM documents d
        LEFT JOIN document_embeddings de ON de.document_id = d.id
        LEFT JOIN document_usage_events ue ON ue.document_id = d.id
        WHERE d.id = $1
          AND d.status != 'deleted'
        GROUP BY d.id
        """,
        doc_id,
    )
    return dict(row) if row else None
