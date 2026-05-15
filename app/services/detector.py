import fitz
import magic
from enum import Enum

_MIN_CHARS_PER_PAGE = 80


class FileKind(str, Enum):
    PDF_TEXT = "pdf_text"
    PDF_SCANNED = "pdf_scanned"
    IMAGE = "image"


def detect_mime(data: bytes) -> str:
    return magic.from_buffer(data, mime=True)


def detect_file_kind(filename: str, data: bytes) -> FileKind:
    mime = detect_mime(data)

    if mime.startswith("image/"):
        return FileKind.IMAGE

    if mime != "application/pdf":
        raise ValueError(
            f"Tipo de archivo no soportado: {mime}. "
            "Solo se aceptan PDFs e imágenes (jpg, png, webp)."
        )

    doc = fitz.open(stream=data, filetype="pdf")
    page_count = doc.page_count

    if page_count == 0:
        doc.close()
        return FileKind.PDF_SCANNED

    total_chars = sum(len(page.get_text("text")) for page in doc)
    doc.close()

    avg = total_chars / page_count
    return FileKind.PDF_TEXT if avg >= _MIN_CHARS_PER_PAGE else FileKind.PDF_SCANNED
