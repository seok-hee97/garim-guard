"""첨부 문서에서 검사할 글을 꺼낸다 — PDF · DOCX · XLSX · PPTX · HWPX.

가림은 첨부 문서 안을 가리지 않는다(문서를 다시 만들지 않는다). 꺼낸 글은 탐지에만 쓰고, 무엇이
나오면 프록시가 정책(`attachments.unmaskable`)에 따라 막거나 경고한다. 이미지 · 레거시 HWP/DOC ·
암호 걸린 PDF 처럼 글을 꺼내지 못하는 첨부는 `attachments.uninspected` 를 따른다.

오피스 · 한글 문서는 표준 라이브러리(zipfile)로 XML 의 글자 노드만 꺼낸다 — XML 파서를 쓰지 않아
엔티티 확장 공격이 닿지 않는다. PDF 는 선택 의존성 `pypdf`(`pip install 'garim-guard[docs]'`)가
있을 때만 읽는다.

★ 전수 검사(2026-10-29) — 0.1.3 까지 프록시는 이진 첨부를 통째로 건너뛰어, PDF · 워드 파일에 실린
  주민번호 · 키가 그대로 상류로 나갔다.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
# 압축을 푼 XML 합계 · 꺼낸 글자 수 상한 — 압축 폭탄과 끝없는 문서를 막는다.
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_CHARS = 2_000_000
MAX_PDF_PAGES = 500

_BREAK = r"</(?:w|a|hp):p>|</row>|</si>"
# 형식 → (읽을 XML 이름, 글자 노드). 글자 노드 뒤 문단 끝은 줄바꿈으로 바꾼다.
_OOXML = {
    "docx": (
        re.compile(r"word/(?:document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml"),
        r"<w:t(?:\s[^>]*)?>(?P<text>[^<]*)</w:t>",
    ),
    "pptx": (
        re.compile(r"ppt/(?:slides/slide\d+|notesSlides/notesSlide\d+)\.xml"),
        r"<a:t(?:\s[^>]*)?>(?P<text>[^<]*)</a:t>",
    ),
    "xlsx": (
        re.compile(r"xl/(?:sharedStrings|worksheets/sheet\d+)\.xml"),
        r"<(?:t|v)(?:\s[^>]*)?>(?P<text>[^<]*)</(?:t|v)>",
    ),
    "hwpx": (
        re.compile(r"Contents/section\d+\.xml"),
        r"<hp:t(?:\s[^>]*)?>(?P<text>[^<]*)</hp:t>",
    ),
}
_MARKERS = {
    "docx": "word/document.xml",
    "pptx": "ppt/presentation.xml",
    "xlsx": "xl/workbook.xml",
    "hwpx": "Contents/content.hpf",
}
DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".pptx", ".xlsx", ".hwpx"})


@dataclass(frozen=True, slots=True)
class Extraction:
    """꺼낸 글. `text` 가 None 이면 꺼내지 못했고 `reason` 이 이유다."""

    kind: str
    text: str | None
    reason: str | None = None


def extract_document(raw: bytes) -> Extraction | None:
    """문서 형식이면 글을 꺼낸다. 문서가 아니면(이미지 · 오디오 · 모르는 이진) None."""
    if raw.startswith(b"%PDF"):
        return _pdf(raw)
    if raw.startswith(b"PK\x03\x04"):
        return _zip_document(raw)
    return None


def _zip_document(raw: bytes) -> Extraction | None:
    if len(raw) > MAX_DOCUMENT_BYTES:
        return Extraction("zip", None, "too_large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, ValueError):
        return None
    with archive:
        names = set(archive.namelist())
        kind = next((kind for kind, marker in _MARKERS.items() if marker in names), None)
        if kind is None and "Contents/section0.xml" in names:
            kind = "hwpx"
        if kind is None:
            return None  # 문서가 아닌 zip — 첨부로 건너뛴다
        member_pattern, text_pattern = _OOXML[kind]
        members = sorted(
            (info for info in archive.infolist() if member_pattern.fullmatch(info.filename)),
            key=lambda info: _natural_key(info.filename),
        )
        if sum(info.file_size for info in members) > MAX_UNCOMPRESSED_BYTES:
            return Extraction(kind, None, "too_large")
        token = re.compile(rf"{text_pattern}|(?P<break>{_BREAK})")
        pieces: list[str] = []
        size = 0
        for info in members:
            try:
                xml = archive.read(info).decode("utf-8", errors="replace")
            except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
                return Extraction(kind, None, "unreadable")  # 암호 · 손상
            for match in token.finditer(xml):
                piece = "\n" if match.group("break") else html.unescape(match.group("text"))
                pieces.append(piece)
                size += len(piece)
                if size > MAX_EXTRACTED_CHARS:
                    return Extraction(kind, None, "too_large")
            pieces.append("\n")
    return Extraction(kind, "".join(pieces).strip())


def _pdf(raw: bytes) -> Extraction:
    if len(raw) > MAX_DOCUMENT_BYTES:
        return Extraction("pdf", None, "too_large")
    try:
        from pypdf import PdfReader
    except ImportError:
        return Extraction("pdf", None, "needs_docs_extra")
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(""):
            return Extraction("pdf", None, "encrypted")
        pieces: list[str] = []
        size = 0
        for page in reader.pages[:MAX_PDF_PAGES]:
            text = page.extract_text() or ""
            pieces.append(text)
            size += len(text)
            if size > MAX_EXTRACTED_CHARS:
                return Extraction("pdf", None, "too_large")
        if len(reader.pages) > MAX_PDF_PAGES:
            return Extraction("pdf", None, "too_large")
    except Exception:  # 손상 · 지원하지 않는 PDF — 검사하지 못한 첨부로 다룬다
        return Extraction("pdf", None, "unreadable")
    return Extraction("pdf", "\n".join(pieces).strip())


def _natural_key(name: str) -> list[object]:
    """`slide10` 이 `slide2` 뒤에 오게."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]
