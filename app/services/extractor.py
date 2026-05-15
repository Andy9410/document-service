import fitz
import pytesseract
import io
import re
from dataclasses import dataclass
from PIL import Image
from pdf2image import convert_from_bytes
from app.services.detector import FileKind

_HEADING_RE = re.compile(
    r"^(\d+[\.\d]*\s+[A-ZÁÉÍÓÚÑ].{3,80}$"
    r"|[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]{5,60}$"
    r"|(?:Ejercicio|Exercise|Problema|Problem|Práctica|Práctico)\s+\d+)",
    re.MULTILINE,
)


@dataclass
class PageBlock:
    page_number: int
    text: str
    section_title: str | None = None


def _extract_heading(text: str) -> str | None:
    m = _HEADING_RE.search(text.strip())
    return m.group(0).strip() if m else None


def extract_text_pdf(data: bytes) -> list[PageBlock]:
    doc = fitz.open(stream=data, filetype="pdf")
    blocks: list[PageBlock] = []
    for page_num, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        if text:
            blocks.append(PageBlock(
                page_number=page_num,
                text=text,
                section_title=_extract_heading(text),
            ))
    doc.close()
    return blocks


def extract_scanned_pdf(data: bytes) -> list[PageBlock]:
    images = convert_from_bytes(data, dpi=300)
    blocks: list[PageBlock] = []
    for page_num, img in enumerate(images, start=1):
        text = pytesseract.image_to_string(img, lang="eng+spa").strip()
        if text:
            blocks.append(PageBlock(
                page_number=page_num,
                text=text,
                section_title=_extract_heading(text),
            ))
    return blocks


def extract_image(data: bytes) -> list[PageBlock]:
    img = Image.open(io.BytesIO(data))
    text = pytesseract.image_to_string(img, lang="eng+spa").strip()
    if not text:
        return []
    return [PageBlock(page_number=1, text=text, section_title=_extract_heading(text))]


def extract(kind: FileKind, data: bytes) -> list[PageBlock]:
    if kind == FileKind.PDF_TEXT:
        return extract_text_pdf(data)
    if kind == FileKind.PDF_SCANNED:
        return extract_scanned_pdf(data)
    if kind == FileKind.IMAGE:
        return extract_image(data)
    raise ValueError(f"FileKind desconocido: {kind}")
