import fitz
import hashlib
import logging
import re
import pytesseract
from PIL import Image
from pdf2image import convert_from_bytes
from app.services.detector import detect_file_kind, FileKind
from app.services.extractor import extract
from app.services.chunker import chunk_blocks, Chunk
from app.services.embedder import embed_texts
from app.services import store
from app.database import get_conn

logger = logging.getLogger(__name__)

_OCR_DPI = 300  # debe coincidir con extract_scanned_pdf()
_EXERCISE_KEYWORDS = {"ejercicio", "exercise", "problema", "problem", "pregunta", "práctica", "práctico", "punto", "item", "inciso", "ej"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _enrich_exercise_bboxes(data: bytes, chunks: list[Chunk]) -> None:
    """Busca en el PDF las coordenadas (bbox) de cada exercise_ref y las
    asigna a los chunks. Usa fitz.search_for() que devuelve la posición
    del texto en la página."""
    if not any(c.exercise_ref for c in chunks):
        return

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        logger.warning("[bbox] No se pudo abrir el PDF para extraer bboxes")
        return

    # Agrupar chunk indices por (exercise_ref, page_number) para buscar una sola vez
    seen: dict[tuple[str, int], list[Chunk]] = {}
    for c in chunks:
        if c.exercise_ref:
            key = (c.exercise_ref, c.page_number)
            seen.setdefault(key, []).append(c)

    for (ref, page_num), group in seen.items():
        if page_num < 1 or page_num > doc.page_count:
            continue
        page = doc[page_num - 1]
        try:
            rects = page.search_for(ref)
        except Exception:
            continue

        if not rects:
            # Fallback: buscar solo el número si el ref completo no funciona
            num_match = re.search(r"(\d+[\.\d]*[a-z]?)", ref)
            if num_match:
                try:
                    rects = page.search_for(num_match.group(1))
                except Exception:
                    pass

        if rects:
            r = rects[0]
            bbox = {"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
            for c in group:
                c.bbox = bbox

    doc.close()

def _ocr_bboxes_for_page(
    img: Image.Image,
    page_points_w: float,
    page_points_h: float,
    refs: set[str],
) -> dict[str, dict]:
    """Ejecuta OCR con image_to_data sobre una página renderizada,
    busca cada exercise_ref y retorna un dict {ref: bbox} en coordenadas
    de PDF points, convertidas desde píxeles OCR."""
    ocr = pytesseract.image_to_data(img, lang="eng+spa", output_type=pytesseract.Output.DICT)
    n = len(ocr["text"])
    if n == 0:
        return {}

    img_w = img.width
    img_h = img.height
    scale_x = page_points_w / img_w if img_w else 1.0
    scale_y = page_points_h / img_h if img_h else 1.0

    # Reconstruir texto de la página para buscar exercise_ref
    words: list[dict] = []
    full_lines: list[str] = []
    current_line = ""
    current_line_words: list[dict] = []

    for i in range(n):
        txt = (ocr["text"][i] or "").strip()
        if not txt:
            continue
        left = ocr["left"][i]
        top = ocr["top"][i]
        w = ocr["width"][i]
        h = ocr["height"][i]
        word_info = {
            "text": txt,
            "x0": left * scale_x,
            "y0": top * scale_y,
            "x1": (left + w) * scale_x,
            "y1": (top + h) * scale_y,
        }
        words.append(word_info)

        if current_line:
            current_line += " "
        current_line += txt
        current_line_words.append(word_info)

        # Detectar fin de línea (block/line change)
        if i + 1 < n:
            next_block = ocr["block_num"][i + 1]
            next_line = ocr["line_num"][i + 1]
            cur_block = ocr["block_num"][i]
            cur_line = ocr["line_num"][i]
            if next_block != cur_block or next_line != cur_line:
                full_lines.append(current_line)
                current_line = ""
                current_line_words = []
        else:
            full_lines.append(current_line)

    full_text = "\n".join(full_lines).lower()

    result = {}
    for ref in refs:
        ref_lower = ref.lower()
        # Buscar en las palabras
        for wi, w in enumerate(words):
            if w["text"].lower() == ref_lower:
                # Match exacto de una palabra
                result[ref] = {"x0": w["x0"], "y0": w["y0"], "x1": w["x1"], "y1": w["y1"]}
                break
        else:
            # Buscar multi-word (ej: "Ejercicio 3" son dos palabras)
            ref_parts = ref_lower.split()
            if len(ref_parts) > 1:
                for wi in range(len(words) - len(ref_parts) + 1):
                    match = True
                    for pi, part in enumerate(ref_parts):
                        if words[wi + pi]["text"].lower() != part:
                            match = False
                            break
                    if match:
                        # Computar bbox combinado
                        xs = [words[wi + pi]["x0"] for pi in range(len(ref_parts))]
                        ys = [words[wi + pi]["y0"] for pi in range(len(ref_parts))]
                        xe = [words[wi + pi]["x1"] for pi in range(len(ref_parts))]
                        ye = [words[wi + pi]["y1"] for pi in range(len(ref_parts))]
                        result[ref] = {
                            "x0": min(xs), "y0": min(ys),
                            "x1": max(xe), "y1": max(ye),
                        }
                        break

    return result


def _enrich_exercise_bboxes_ocr(data: bytes, chunks: list[Chunk]) -> None:
    """Para PDFs escaneados: renderiza cada página con pdf2image, corre OCR
    con image_to_data para obtener coordenadas de palabras, y asigna bbox
    a los chunks que contienen exercise_ref."""
    if not any(c.exercise_ref for c in chunks):
        return

    # Agrupar chunks por página
    by_page: dict[int, list[Chunk]] = {}
    refs_by_page: dict[int, set[str]] = {}
    for c in chunks:
        if c.exercise_ref:
            by_page.setdefault(c.page_number, []).append(c)
            refs_by_page.setdefault(c.page_number, set()).add(c.exercise_ref)

    try:
        images = convert_from_bytes(data, dpi=_OCR_DPI)
    except Exception as e:
        logger.warning("[bbox/ocr] Error al convertir PDF escaneado: %s", e)
        return

    # Obtener dimensiones de página desde el PDF original (en points)
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        doc = None

    for page_num, img in enumerate(images, start=1):
        if page_num not in refs_by_page:
            continue

        if doc and page_num <= len(doc):
            page_rect = doc[page_num - 1].rect
            pw, ph = page_rect.width, page_rect.height
        else:
            # Fallback: estimar desde imagen
            pw = img.width * 72.0 / _OCR_DPI
            ph = img.height * 72.0 / _OCR_DPI

        bboxes = _ocr_bboxes_for_page(img, pw, ph, refs_by_page[page_num])
        for c in by_page.get(page_num, []):
            if c.exercise_ref and c.exercise_ref in bboxes:
                c.bbox = bboxes[c.exercise_ref]

    if doc:
        doc.close()


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

        # Enriquecer chunks con bbox de ejercicios desde el PDF original
        if kind == FileKind.PDF_TEXT:
            _enrich_exercise_bboxes(data, chunks)
        elif kind == FileKind.PDF_SCANNED:
            _enrich_exercise_bboxes_ocr(data, chunks)

        embeddings = await embed_texts([f"passage: {c.text}" for c in chunks])

        async with conn.transaction():
            doc_id = await store.insert_document(
                user_email=user_email,
                filename=filename,
                file_type=kind.value,
                content_hash=content_hash,
                page_count=page_count,
                summary=summary,
                content_data=data,
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
