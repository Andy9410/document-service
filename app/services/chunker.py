import re
import tiktoken
from dataclasses import dataclass
from app.services.extractor import PageBlock
from app.config import get_settings

_EXERCISE_RE = re.compile(
    r"(?:Ejercicio|Exercise|Problema|Problem|Ej\.?|Práctica|Práctico)\s*(\d+[\.\d]*)",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_enc = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_number: int
    section_title: str | None
    exercise_ref: str | None


def _tokens(text: str) -> int:
    return len(_enc.encode(text))


def _last_n_tokens(text: str, n: int) -> str:
    ids = _enc.encode(text)
    return _enc.decode(ids[-n:]) if len(ids) > n else text


def _detect_exercise(text: str) -> str | None:
    m = _EXERCISE_RE.search(text)
    return m.group(0).strip() if m else None


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text) if s.strip()]


def chunk_blocks(blocks: list[PageBlock]) -> list[Chunk]:
    s = get_settings()
    max_tokens = s.chunk_size        # 512 real tokens
    overlap_tokens = s.chunk_overlap  # 64 real tokens
    min_len = s.min_chunk_length     # 80 chars — sanity filter for very short chunks

    chunks: list[Chunk] = []
    index = 0
    carry = ""

    for block in blocks:
        if block.section_title:
            carry = ""

        full = (carry + " " + block.text).strip() if carry else block.text
        sentences = _split_sentences(full)
        parts: list[str] = []
        token_count = 0

        for sentence in sentences:
            stokens = _tokens(sentence)
            if token_count + stokens > max_tokens and parts:
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
                carry_text = _last_n_tokens(text, overlap_tokens)
                parts = [carry_text, sentence]
                token_count = _tokens(carry_text) + stokens
            else:
                parts.append(sentence)
                token_count += stokens

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
            carry = _last_n_tokens(text, overlap_tokens)

    return chunks
