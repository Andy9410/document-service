import re
import logging
import tiktoken
from dataclasses import dataclass
from app.services.extractor import PageBlock
from app.config import get_settings

log = logging.getLogger(__name__)

_EXERCISE_RE = re.compile(
    r"(?:ejercicio|exercise|problema|problem|pregunta|práctica|práctico|punto|item|inciso|ej\.?)\s*(\d+[\.\d]*[a-z]?)",
    re.IGNORECASE,
)
_PARA_BREAK_RE = re.compile(r"\n{2,}")
_SOFT_WRAP_RE = re.compile(r"(?<!\n)\n(?!\n)")
_enc = tiktoken.get_encoding("cl100k_base")

try:
    import spacy
    _nlp = spacy.load("es_core_news_sm", disable=["ner", "tagger", "parser", "lemmatizer", "attribute_ruler"])
    _nlp.add_pipe("sentencizer")
    log.info("[chunker] spaCy es_core_news_sm loaded")
    def _split_sentences(text: str) -> list[str]:
        return [s.text.strip() for s in _nlp(text).sents if s.text.strip()]
except Exception:
    log.warning("[chunker] spaCy not available, falling back to regex sentence splitter")
    _SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
    def _split_sentences(text: str) -> list[str]:  # type: ignore[misc]
        return [s for s in _SENTENCE_RE.split(text) if s.strip()]


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_number: int
    section_title: str | None
    exercise_ref: str | None
    bbox: dict | None = None


def _tokens(text: str) -> int:
    return len(_enc.encode(text))


def _exercise_ref(text: str) -> str | None:
    m = _EXERCISE_RE.search(text)
    return m.group(0).strip() if m else None


def _normalize(text: str) -> str:
    """Join soft-wrap newlines into spaces; preserve paragraph breaks."""
    text = _SOFT_WRAP_RE.sub(" ", text)
    return re.sub(r" {2,}", " ", text).strip()


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARA_BREAK_RE.split(_normalize(text)) if p.strip()]


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text) if s.strip()]


def chunk_blocks(blocks: list[PageBlock]) -> list[Chunk]:
    s = get_settings()
    max_tokens = s.chunk_size        # 384
    overlap_tokens = s.chunk_overlap  # 38
    min_len = s.min_chunk_length     # 80

    chunks: list[Chunk] = []
    index = 0

    # Mutable working state
    parts: list[str] = []
    tok_count = 0
    cur_page = 1
    cur_section: str | None = None
    cur_exercise: str | None = None

    def flush() -> None:
        nonlocal index, parts, tok_count
        if not parts:
            return
        text = " ".join(parts).strip()
        if len(text) >= min_len:
            chunks.append(Chunk(
                text=text,
                chunk_index=index,
                page_number=cur_page,
                section_title=cur_section,
                exercise_ref=cur_exercise or _exercise_ref(text),
            ))
            index += 1
        # Overlap: carry whole paragraphs from the end up to overlap_tokens
        overlap: list[str] = []
        overlap_tok = 0
        for part in reversed(parts):
            t = _tokens(part)
            if overlap_tok + t > overlap_tokens:
                break
            overlap.insert(0, part)
            overlap_tok += t
        parts = overlap
        tok_count = overlap_tok

    def push(unit: str) -> None:
        """Add a sentence/paragraph to the current chunk, flushing if needed."""
        nonlocal tok_count
        unit_tok = _tokens(unit)
        if tok_count + unit_tok > max_tokens and parts:
            flush()
        parts.append(unit)
        tok_count += unit_tok

    for block in blocks:
        cur_page = block.page_number
        if block.section_title:
            cur_section = block.section_title

        for para in _split_paragraphs(block.text):
            para_ref = _exercise_ref(para)

            # New exercise boundary → flush immediately, switch exercise context
            if para_ref and para_ref != cur_exercise:
                flush()
                cur_exercise = para_ref

            para_tok = _tokens(para)
            if para_tok > max_tokens:
                # Paragraph too big → split into sentences
                for sent in _split_sentences(para):
                    push(sent)
            else:
                push(para)

    flush()
    return chunks
