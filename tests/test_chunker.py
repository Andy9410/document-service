import unittest
import sys
import types


fake_tiktoken = types.ModuleType("tiktoken")
sys.modules.setdefault("fitz", types.ModuleType("fitz"))
sys.modules.setdefault("pytesseract", types.ModuleType("pytesseract"))

fake_pil = types.ModuleType("PIL")
fake_pil_image = types.ModuleType("PIL.Image")
fake_pil.Image = fake_pil_image
sys.modules.setdefault("PIL", fake_pil)
sys.modules.setdefault("PIL.Image", fake_pil_image)

fake_pdf2image = types.ModuleType("pdf2image")
fake_pdf2image.convert_from_bytes = lambda *args, **kwargs: []
sys.modules.setdefault("pdf2image", fake_pdf2image)
sys.modules.setdefault("magic", types.ModuleType("magic"))

fake_config = types.ModuleType("app.config")


class _FakeSettings:
    chunk_size = 384
    chunk_overlap = 48
    min_chunk_length = 80


fake_config.get_settings = lambda: _FakeSettings()
sys.modules.setdefault("app.config", fake_config)


class _FakeEncoding:
    def encode(self, text: str) -> list[str]:
        return text.split()


def _get_encoding(_: str) -> _FakeEncoding:
    return _FakeEncoding()


fake_tiktoken.get_encoding = _get_encoding
sys.modules.setdefault("tiktoken", fake_tiktoken)

from app.services.chunker import chunk_blocks
from app.services.extractor import PageBlock


class ChunkerTest(unittest.TestCase):
    def test_preserves_short_document_as_single_chunk(self) -> None:
        blocks = [
            PageBlock(
                page_number=1,
                text="Montevideo es la capital de Uruguay.",
                section_title=None,
            )
        ]

        chunks = chunk_blocks(blocks)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "Montevideo es la capital de Uruguay.")
        self.assertEqual(chunks[0].page_number, 1)


if __name__ == "__main__":
    unittest.main()
