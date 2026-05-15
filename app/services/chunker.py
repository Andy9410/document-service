import re
from dataclasses import dataclass
from app.services.extractor import PageBlock
from app.config import get_settings

_EXERCISE_RE = re.compile(
    r"(?:Ejercicio|Exercise|Problema|Problem|Ej\.?|Práctica|Práctico)\s*(\d+[\.\d]*)",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_number: int
    section_title: str | None
    exercise_ref: str | None


def _detect_exercise(text: str) -> str | None:
    m = _EXERCISE_RE.search(text)
    return m.group(0).strip() if m else None


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text) if s.strip()]


def chunk_blocks(blocks: list[PageBlock]) -> list[Chunk]:
    s = get_settings()
    max_chars = s.chunk_size * 4
    overlap_chars = s.chunk_overlap * 4
    min_len = s.min_chunk_length

    chunks: list[Chunk] = []
    index = 0
    carry = ""

    for block in blocks:
        if block.section_title:
            carry = ""

        full = (carry + " " + block.text).strip() if carry else block.text
        sentences = _split_sentences(full)
        parts: list[str] = []
        length = 0

        for sentence in sentences:
            slen = len(sentence)
            if length + slen > max_chars and parts:
                text = " ".join(parts).strip()
                if len(text) >= min_len:
                    chunks.append(Chunk(
                        text=text,
                        chunk_index=index,
                        page_number=block.page_number,
                        section_title=block.section_title,
                        exercise_ref=_detect_exercise(text),
                    ))
                    index += 1
                carry_text = text[-overlap_chars:] if len(text) > overlap_chars else text
                parts = [carry_text, sentence]
                length = len(carry_text) + slen
            else:
                parts.append(sentence)
                length += slen

        if parts:
            text = " ".join(parts).strip()
            if len(text) >= min_len:
                chunks.append(Chunk(
                    text=text,
                    chunk_index=index,
                    page_number=block.page_number,
                    section_title=block.section_title,
                    exercise_ref=_detect_exercise(text),
                ))
                index += 1
            carry = text[-overlap_chars:] if len(text) > overlap_chars else text

    return chunks
