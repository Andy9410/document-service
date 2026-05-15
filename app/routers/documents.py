from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, status
from app.auth import require_user
from app.models import UploadResult, DocumentOut
from app.services.pipeline import ingest_document
from app.services import store
from app.database import get_conn
from app.config import get_settings

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=list[UploadResult])
async def upload_documents(
    files: list[UploadFile] = File(...),
    user_email: str = Depends(require_user),
):
    settings = get_settings()
    max_size = settings.max_file_size_mb * 1024 * 1024

    if len(files) > settings.max_files_per_upload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Máximo {settings.max_files_per_upload} archivos por solicitud.",
        )

    results = []
    for f in files:
        data = await f.read()
        if len(data) > max_size:
            results.append(UploadResult(
                filename=f.filename or "archivo",
                status="error",
                message=f"El archivo supera el límite de {settings.max_file_size_mb} MB.",
            ))
            continue

        result = await ingest_document(user_email, f.filename or "archivo", data)
        results.append(UploadResult(
            document_id=result.get("document_id"),
            filename=f.filename or "archivo",
            chunk_count=result.get("chunk_count", 0),
            page_count=result.get("page_count"),
            file_type=result.get("file_type"),
            status=result.get("status", "error"),
            message=result.get("message"),
        ))

    return results


@router.get("", response_model=list[DocumentOut])
async def list_documents(user_email: str = Depends(require_user)):
    async with get_conn() as conn:
        docs = await store.get_user_documents(user_email, conn)
    return [DocumentOut(**d) for d in docs]


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: int, user_email: str = Depends(require_user)):
    async with get_conn() as conn:
        deleted = await store.delete_document(doc_id, user_email, conn)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado.")
