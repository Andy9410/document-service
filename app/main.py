import os
import sentry_sdk


from sentry_sdk.integrations.fastapi import (
    FastApiIntegration,
)

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_pool, close_pool, get_pool
from app.routers import documents, search, ocr

logging.basicConfig(level=logging.INFO)

sentry_sdk.init(
    dsn="https://569716aec27a317c22b5e68562e6a5ed@o4511384546377728.ingest.us.sentry.io/4511408058007552",
    # Add data like request headers and IP for users,
    # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
    send_default_pii=True,
)



@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()

    # Asegurar columnas necesarias para el visor PDF
    async with get_pool().acquire() as conn:
        await conn.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_data BYTEA"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_usage_events (
                id BIGSERIAL PRIMARY KEY,
                document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                user_email VARCHAR(150) NOT NULL,
                action VARCHAR(32) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_usage_events_document_id_created_at "
            "ON document_usage_events(document_id, created_at DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_usage_events_user_email_created_at "
            "ON document_usage_events(user_email, created_at DESC)"
        )

    yield
    await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Document Service",
        description="Ingesta de documentos y búsqueda RAG para el tutor IA",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:5174",
            "http://localhost:8080",
            "http://localhost:8082",
            "https://learnsoft.uy",
            "https://learnsoft-academy.fly.dev",
            "https://codebuff-academy.github.io",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    app.include_router(documents.router)
    app.include_router(search.router)
    app.include_router(ocr.router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "document-service"}
    return app

app = create_app()
