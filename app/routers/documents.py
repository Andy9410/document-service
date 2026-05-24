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
    Response,
    status,
)

from fastapi.responses import Response

from app.auth import require_user
from app.models import UploadResult, DocumentOut
from app.services.pipeline import ingest_document, _ocr_bboxes_for_page
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
                data=data
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


@router.get(
    "/{doc_id}/download",
    response_class=Response,
)
async def download_document(
        doc_id: int,
        user_email: str = Depends(require_user),
):
    async with get_conn() as conn:
        data = await store.get_document_data(doc_id, user_email, conn)

    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Documento no encontrado.",
        )

    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=\"document-{doc_id}.pdf\"",
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get(
    "/{doc_id}/exercises",
)
async def list_exercises(
        doc_id: int,
        user_email: str = Depends(require_user),
):
    async with get_conn() as conn:
        raw = await store.get_document_exercises(doc_id, user_email, conn)

    results = []
    for ex in raw:
        if not ex["exercise_ref"]:
            continue
        item = {
            "number": _extract_num(ex["exercise_ref"]),
            "page": ex["page_number"],
            "title": ex["section_title"],
        }
        # bbox viene de metadata->'bbox' como dict o None
        bbox = ex.get("bbox")
        if bbox and isinstance(bbox, dict):
            item["bbox"] = {
                "x0": bbox.get("x0"),
                "y0": bbox.get("y0"),
                "x1": bbox.get("x1"),
                "y1": bbox.get("y1"),
            }
        results.append(item)

    return results


def _extract_num(ref: str) -> str:
    """Extrae solo el número de un exercise_ref como 'Ejercicio 3' → '3'."""
    import re
    m = re.search(r"(\d+[\.\d]*[a-z]?)", ref)
    return m.group(1) if m else ref


@router.post("/repair-bboxes")
async def repair_bboxes(
        user_email: str = Depends(require_user),
):
    """Procesa todos los documentos del usuario que tienen exercise_ref en chunks
    pero no tienen bbox todavía. Reabre el PDF y usa fitz.search_for() para
    encontrar las coordenadas."""

    async with get_conn() as conn:
        docs = await store.get_documents_needing_bbox(user_email, conn)

    if not docs:
        return {"repaired": 0, "message": "No hay documentos pendientes de reparación."}

    total_updated = 0
    results = []

    for doc in docs:
        doc_id = doc["id"]
        filename = doc["filename"]
        file_type = doc["file_type"]

        # Leer el PDF binario
        async with get_conn() as conn:
            data = await store.get_document_data(doc_id, user_email, conn)
            if not data:
                results.append({"doc_id": doc_id, "filename": filename, "status": "skipped", "reason": "sin content_data"})
                continue

        # Leer chunks que necesitan bbox
        async with get_conn() as conn:
            raw_chunks = await store.get_chunks_needing_bbox(doc_id, conn)

        if not raw_chunks:
            results.append({"doc_id": doc_id, "filename": filename, "status": "ok", "updated": 0})
            continue

        updated = 0

        if file_type == "pdf_scanned":
            # --- OCR path: renderizar páginas y usar image_to_data ---
            try:
                from pdf2image import convert_from_bytes
                images = convert_from_bytes(data, dpi=300)
            except Exception as e:
                results.append({"doc_id": doc_id, "filename": filename, "status": "error", "reason": f"error al renderizar PDF escaneado: {e}"})
                continue

            # Agrupar chunks por (exercise_ref, page_number)
            grouped: dict[tuple[str, int], list[dict]] = {}
            for rc in raw_chunks:
                meta = rc.get("metadata") or {}
                ref = meta.get("exercise_ref")
                if not ref:
                    continue
                key = (ref, rc["page_number"])
                grouped.setdefault(key, []).append(rc)

            # Obtener dimensiones de página en points
            try:
                pdf_doc = fitz.open(stream=data, filetype="pdf")
            except Exception:
                pdf_doc = None

            for (ref, page_num), group in grouped.items():
                if page_num < 1 or page_num > len(images):
                    continue

                img = images[page_num - 1]
                if pdf_doc and page_num <= len(pdf_doc):
                    pw = pdf_doc[page_num - 1].rect.width
                    ph = pdf_doc[page_num - 1].rect.height
                else:
                    pw = img.width * 72.0 / 300
                    ph = img.height * 72.0 / 300

                # Correr OCR con coordenadas en esta página
                bboxes_dict = _ocr_bboxes_for_page(img, pw, ph, {ref})
                bbox = bboxes_dict.get(ref)
                if bbox:
                    async with get_conn() as conn:
                        for rc in group:
                            await store.update_chunk_metadata(rc["id"], {"bbox": bbox}, conn)
                            updated += 1

            if pdf_doc:
                pdf_doc.close()

        else:
            # --- Text PDF path: fitz.search_for() ---
            try:
                pdf = fitz.open(stream=data, filetype="pdf")
            except Exception as e:
                results.append({"doc_id": doc_id, "filename": filename, "status": "error", "reason": str(e)})
                continue

            # Agrupar chunks por (exercise_ref, page_number)
            grouped = {}
            for rc in raw_chunks:
                meta = rc.get("metadata") or {}
                ref = meta.get("exercise_ref")
                if not ref:
                    continue
                key = (ref, rc["page_number"])
                grouped.setdefault(key, []).append(rc)

            for (ref, page_num), group in grouped.items():
                if page_num < 1 or page_num > len(pdf):
                    continue
                page = pdf[page_num - 1]
                try:
                    rects = page.search_for(ref)
                except Exception:
                    continue

                if not rects:
                    # Fallback: buscar solo el número
                    num_match = re.search(r"(\d+[\.\d]*[a-z]?)", ref)
                    if num_match:
                        try:
                            rects = page.search_for(num_match.group(1))
                        except Exception:
                            pass

                if rects:
                    r = rects[0]
                    bbox = {"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
                    async with get_conn() as conn:
                        for rc in group:
                            await store.update_chunk_metadata(rc["id"], {"bbox": bbox}, conn)
                            updated += 1

            pdf.close()

        total_updated += updated
        results.append({"doc_id": doc_id, "filename": filename, "status": "ok", "updated": updated})

    return {
        "repaired": total_updated,
        "documents": results,
    }


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


@router.get(
    "/{doc_id}/download",
)
async def download_document(
        doc_id: int,
        user_email: str = Depends(require_user),
):
    async with get_conn() as conn:

        data = await store.get_content_data(
            doc_id,
            user_email,
            conn,
        )

    if data is None:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Documento no encontrado o sin contenido binario.",
        )

    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Content-Length": str(len(data)),
        },
    )