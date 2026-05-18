import os
import sentry_sdk

from sentry_sdk.integrations.fastapi import (
    FastApiIntegration,
)
import io
import time
import fitz
import pytesseract

from PIL import Image

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Depends,
    HTTPException,
    status,
)

from app.auth import require_user
from app.models import UploadResult, DocumentOut
from app.services.pipeline import ingest_document
from app.services import store
from app.database import get_conn
from app.config import get_settings

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
)

# Evita matar el container de Fly
MAX_OCR_PAGES = 5
OCR_DPI = 120


@router.options("/{path:path}")
async def options_handler(path: str):
    return {"ok": True}


@router.post(
    "/upload",
    response_model=list[UploadResult],
)
async def upload_documents(
        files: list[UploadFile] = File(...),
        user_email: str = Depends(require_user),
):
    settings = get_settings()

    max_size = (
            settings.max_file_size_mb
            * 1024
            * 1024
    )

    if len(files) > settings.max_files_per_upload:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Máximo "
                f"{settings.max_files_per_upload} "
                f"archivos por solicitud."
            ),
        )

    results = []

    for f in files:

        start_time = time.time()

        try:

            filename = f.filename or "archivo"

            print("=" * 80)
            print(f"PROCESSING: {filename}")

            data = await f.read()

            # Archivo vacío
            if not data:

                results.append(
                    UploadResult(
                        filename=filename,
                        status="error",
                        message="El archivo está vacío.",
                    )
                )

                continue

            # Tamaño
            if len(data) > max_size:

                results.append(
                    UploadResult(
                        filename=filename,
                        status="error",
                        message=(
                            f"El archivo supera "
                            f"el límite de "
                            f"{settings.max_file_size_mb} MB."
                        ),
                    )
                )

                continue

            # Tipos permitidos
            allowed_types = [
                "application/pdf",
                "image/png",
                "image/jpeg",
                "image/jpg",
            ]

            if f.content_type not in allowed_types:

                results.append(
                    UploadResult(
                        filename=filename,
                        status="error",
                        message="Formato no soportado.",
                    )
                )

                continue

            extracted_text = ""

            # ==================================================
            # PDFs
            # ==================================================

            if f.content_type == "application/pdf":

                try:

                    pdf = fitz.open(
                        stream=data,
                        filetype="pdf",
                    )

                    print(f"PDF PAGES: {len(pdf)}")

                    # Intentar extracción normal
                    for page in pdf:
                        extracted_text += page.get_text()

                    print(
                        f"TEXT LENGTH: "
                        f"{len(extracted_text)}"
                    )

                    # OCR SOLO si no hay texto
                    if len(extracted_text.strip()) < 50:

                        print("OCR FALLBACK")

                        extracted_text = ""

                        for page_index, page in enumerate(pdf):

                            if page_index >= MAX_OCR_PAGES:

                                print(
                                    "OCR PAGE LIMIT REACHED"
                                )

                                break

                            print(
                                f"OCR PAGE "
                                f"{page_index + 1}"
                            )

                            pix = page.get_pixmap(
                                dpi=OCR_DPI,
                            )

                            img = Image.open(
                                io.BytesIO(
                                    pix.tobytes("png")
                                )
                            )

                            page_text = (
                                pytesseract.image_to_string(
                                    img,
                                    lang="spa",
                                )
                            )

                            extracted_text += page_text

                except Exception as pdf_error:

                    print(
                        f"PDF ERROR: {pdf_error}"
                    )

                    results.append(
                        UploadResult(
                            filename=filename,
                            status="error",
                            message=(
                                f"Error PDF: "
                                f"{str(pdf_error)}"
                            ),
                        )
                    )

                    continue

            # ==================================================
            # Imágenes
            # ==================================================

            elif f.content_type.startswith("image/"):

                try:

                    print("OCR IMAGE")

                    img = Image.open(
                        io.BytesIO(data)
                    )

                    extracted_text = (
                        pytesseract.image_to_string(
                            img,
                            lang="spa",
                        )
                    )

                except Exception as image_error:

                    print(
                        f"IMAGE ERROR: "
                        f"{image_error}"
                    )

                    results.append(
                        UploadResult(
                            filename=filename,
                            status="error",
                            message=(
                                f"Error imagen: "
                                f"{str(image_error)}"
                            ),
                        )
                    )

                    continue

            # ==================================================
            # Debug
            # ==================================================

            print(
                f"FINAL TEXT LENGTH: "
                f"{len(extracted_text)}"
            )

            print(
                extracted_text[:1000]
            )

            # ==================================================
            # Normalización matemática
            # ==================================================

            normalized_text = (
                extracted_text
                .replace("²", "^2")
                .replace("³", "^3")
                .replace("¹", "^1")
                .replace("ⁿ", "^n")
                .replace("⁺", "^+")
                .replace("⁻", "^-")
            )

            # ==================================================
            # Ingesta
            # ==================================================

            print("INGEST START")

            result = await ingest_document(
                user_email=user_email,
                filename=filename,
                data=data,
                extracted_text=extracted_text,
                normalized_text=normalized_text,
                content_type=f.content_type,
            )

            print("INGEST OK")

            elapsed = (
                    time.time()
                    - start_time
            )

            print(
                f"TOTAL TIME: "
                f"{elapsed:.2f}s"
            )

            results.append(
                UploadResult(
                    document_id=result.get(
                        "document_id"
                    ),
                    filename=filename,
                    chunk_count=result.get(
                        "chunk_count",
                        0,
                    ),
                    page_count=result.get(
                        "page_count"
                    ),
                    file_type=result.get(
                        "file_type"
                    ),
                    status=result.get(
                        "status",
                        "success",
                    ),
                    message=result.get(
                        "message",
                        "Documento procesado correctamente.",
                    ),
                )
            )

        except Exception as e:

            print(f"GLOBAL ERROR: {e}")

            results.append(
                UploadResult(
                    filename=f.filename or "archivo",
                    status="error",
                    message=str(e),
                )
            )

    return results


@router.get(
    "",
    response_model=list[DocumentOut],
)
async def list_documents(
        user_email: str = Depends(require_user),
):
    async with get_conn() as conn:

        docs = await store.get_user_documents(
            user_email,
            conn,
        )

    return [
        DocumentOut(**d)
        for d in docs
    ]


@router.delete(
    "/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
        doc_id: int,
        user_email: str = Depends(require_user),
):
    async with get_conn() as conn:

        deleted = await store.delete_document(
            doc_id,
            user_email,
            conn,
        )

    if not deleted:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Documento no encontrado.",
        )