"""첨부 문서에서 검사할 글을 꺼낸다 — PDF · DOCX · XLSX · PPTX · HWPX · ODF.

가림은 첨부 문서 안을 가리지 않는다(문서를 다시 만들지 않는다). 꺼낸 글은 탐지에만 쓰고, 무엇이
나오면 프록시가 정책(`attachments.unmaskable`)에 따라 막거나 경고한다. 문서인데 읽지 못하면
(`too_large` · `unreadable` · `encrypted` · `timeout` · `needs_docs_extra`) `attachments.unreadable`
(기본 block)을, 글이 없거나(`no_text`) 문서가 아닌 이진이면 `attachments.uninspected` 를 따른다.

오피스 · 한글 · ODF 문서는 표준 라이브러리(zipfile)와 선형 토크나이저로 XML 의 글자만 꺼낸다 —
XML 파서를 쓰지 않아 엔티티 확장이 닿지 않는다. PDF 는 선택 의존성 `pypdf`(`garim-guard[docs]`)로,
별도 프로세스에서 시간 제한을 두고 읽는다.

★ 전수 검사(2026-10-29) — 0.1.3 까지 프록시는 이진 첨부를 통째로 건너뛰어, PDF · 워드 파일에 실린
  주민번호 · 키가 그대로 상류로 나갔다. 처음 판은 검토에서 결함이 여럿 나왔다 — 일부러 크게 · 깨지게
  만든 문서가 "검사 못 함 → 허용" 으로 새고, 글자 노드 정규식이 제곱 시간이었고, 엑셀 셀이 붙어
  주민번호를 놓치거나 카드 번호를 지어냈다. 지금 판이 그 결함을 막는다.
"""

from __future__ import annotations

import html
import io
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
# 압축을 푼 XML 합계 · 멤버 수 · 꺼낸 글자 수 상한 — 압축 폭탄과 끝없는 문서를 막는다.
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 4_000
MAX_EXTRACTED_CHARS = 2_000_000
MAX_PDF_PAGES = 500
PDF_TIMEOUT_SECONDS = 20.0
MAX_NESTING = 2

# 읽지 못한 문서 — 정책 `attachments.unreadable`(기본 block). 그 밖의 사유는 `uninspected`.
UNREADABLE_REASONS = frozenset(
    {"too_large", "unreadable", "encrypted", "timeout", "needs_docs_extra"}
)
# 읽지 못했을 때 바이트가 텍스트면 그 텍스트를 대신 검사해도 되는 사유 — `%PDF` 로 시작하는 메모,
# `[docs]` 없이 받은 ASCII PDF. 암호 · 시간 초과 · 너무 큼 · 글 없음은 대신 읽을 글이 없다.
TEXT_FALLBACK_REASONS = frozenset({"unreadable", "needs_docs_extra"})
DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".pptx", ".xlsx", ".hwpx", ".odt", ".ods", ".odp"})

# 글이 없는 XML(스타일 · 테마 · 설정 · 관계).
_NO_TEXT_PART = re.compile(
    r"(?:^|/)(?:styles|styleswitheffects|theme\d*|settings|websettings|fonttable|numbering|"
    r"presprops|viewprops|tablestyles|calcchain|manifest|header\.xml)\.xml$|\.rels$|"
    r"(?:^|/)_rels/|\[content_types\]\.xml$"
)
_EMBEDDED_DOCUMENT = re.compile(r"\.(?:docx|xlsx|pptx|hwpx|odt|ods|odp)$")
# 태그 · 글자 토큰. 태그 안에 `<` · `>` 를 허락하지 않아 어떤 입력에도 선형이다.
_TOKEN = re.compile(r"<(/?)(?:[\w.-]+:)?([\w.-]+)([^<>]*)>|([^<]+)|<")
_ATTRIBUTE_TEXT = re.compile(
    r"""\b(?:descr|title|alt)\s*=\s*"([^"]*)"|\b(?:descr|title|alt)\s*=\s*'([^']*)'"""
)
_CELL_TYPE = re.compile(r"""\bt\s*=\s*["']([^"']*)["']""")
_TEXT_TAGS = frozenset({"t", "deltext", "instrtext"})
_LINE_TAGS = frozenset({"p", "tr", "row", "si", "h", "list-item", "table-row"})
_LINE_BREAKS = frozenset({"br", "cr", "linebreak", "line-break"})
_CELL_TAGS = frozenset({"tc", "c", "tab", "table-cell", "s"})


@dataclass(frozen=True, slots=True)
class Extraction:
    """꺼낸 글. `text` 가 None 이면 꺼내지 못했고 `reason` 이 이유다."""

    kind: str
    text: str | None
    reason: str | None = None


class _Budget:
    def __init__(self, chars: int, depth: int = 0) -> None:
        self.chars = chars
        self.depth = depth


class _TooLarge(Exception):
    pass


def extract_document(raw: bytes, *, max_chars: int = MAX_EXTRACTED_CHARS) -> Extraction | None:
    """문서 형식이면 글을 꺼낸다. 문서가 아니면(이미지 · 오디오 · 모르는 이진 · 보통 zip) None."""
    if len(raw) > MAX_DOCUMENT_BYTES and (raw[:4] == b"PK\x03\x04" or b"%PDF-" in raw[:1024]):
        return Extraction("document", None, "too_large")
    if b"%PDF-" in raw[:1024]:  # 앞에 줄바꿈 · BOM 이 붙은 PDF 도 PDF 다
        return _pdf(raw, max_chars)
    if raw.startswith(b"PK\x03\x04"):
        return _zip_document(raw, _Budget(max_chars))
    return None


# ── 오피스 · 한글 · ODF ──────────────────────────────────────────────────────


def _zip_kind(names: dict[str, str], archive: zipfile.ZipFile) -> str | None:
    """zip 이 문서인가. 이름은 대소문자를 가리지 않는다(`Word/Document.xml`)."""
    # 주 파트가 있으면 `[Content_Types].xml` 을 빼고 보내도 문서로 읽는다.
    for kind, marker in (
        ("docx", "word/document.xml"),
        ("pptx", "ppt/presentation.xml"),
        ("xlsx", "xl/workbook.xml"),
    ):
        if marker in names:
            return kind
    if "[content_types].xml" in names:
        for kind, folder in (("docx", "word/"), ("pptx", "ppt/"), ("xlsx", "xl/")):
            if any(name.startswith(folder) for name in names):
                return kind
        return "office"  # 주 파트를 `_rels` 로 옮긴 문서도 문서다 — 모든 XML 을 읽는다
    if "mimetype" in names:
        try:
            mimetype = archive.read(names["mimetype"])[:100].decode("ascii", errors="replace")
        except Exception:
            return None
        if "hwp" in mimetype:
            return "hwpx"
        if "opendocument" in mimetype:
            return "odf"
    if any(name.startswith("contents/section") for name in names):
        return "hwpx"
    return None


def _zip_document(raw: bytes, budget: _Budget) -> Extraction | None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except Exception:  # BadZipFile · 지원하지 않는 압축 · 깨진 중앙 디렉터리
        return None if budget.depth == 0 else Extraction("document", None, "unreadable")
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            return Extraction("document", None, "too_large")
        names = {info.filename.lower(): info.filename for info in infos}
        kind = _zip_kind(names, archive)
        if kind is None:
            return None  # 문서가 아닌 zip — 첨부로 건너뛴다
        parts = [
            info
            for info in infos
            if info.filename.lower().endswith(".xml")
            and not _NO_TEXT_PART.search(info.filename.lower())
        ]
        embedded = [info for info in infos if _EMBEDDED_DOCUMENT.search(info.filename.lower())]
        if sum(info.file_size for info in parts + embedded) > MAX_UNCOMPRESSED_BYTES:
            return Extraction(kind, None, "too_large")
        # 엑셀 공유 문자열을 먼저 읽는다 — 셀은 번호로만 가리킨다.
        shared: list[str] = []
        try:
            for info in parts:
                if info.filename.lower().endswith("sharedstrings.xml"):
                    shared = _xml_text(_read(archive, info), budget, collect_items=True)
            # 셀이 가리키지 않는 공유 문자열에도 값을 숨길 수 있다 — 전부 검사한다. 셀은 아래에서
            # 행 순서대로 한 번 더 나온다(행 문맥).
            pieces: list[str] = [item for text in shared for item in (text, "\n")]
            for info in sorted(parts, key=lambda item: _natural_key(item.filename)):
                if info.filename.lower().endswith("sharedstrings.xml"):
                    continue
                every_text = kind == "odf" or "docprops/" in info.filename.lower()
                pieces.extend(_xml_text(_read(archive, info), budget, shared, every_text))
                pieces.append("\n")
            for info in embedded:
                if budget.depth + 1 >= MAX_NESTING:
                    return Extraction(kind, None, "too_large")
                inner = _zip_document(_read(archive, info), _Budget(budget.chars, budget.depth + 1))
                if inner is not None:
                    if inner.text is None:
                        return Extraction(kind, None, inner.reason)
                    budget.chars -= len(inner.text)
                    pieces.extend((inner.text, "\n"))
        except _TooLarge:
            return Extraction(kind, None, "too_large")
        except (
            Exception
        ):  # 깨진 멤버(CRC) · 암호 · 지원하지 않는 압축 — 일부만 읽은 문서는 믿지 않는다
            return Extraction(kind, None, "unreadable")
    text = re.sub(r"[ \t]*\n[\n \t]*", "\n", "".join(pieces)).strip()
    return Extraction(kind, text) if text else Extraction(kind, None, "no_text")


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    return archive.read(info)


def _xml_text(
    data: bytes,
    budget: _Budget,
    shared: list[str] | None = None,
    every_text: bool = False,
    collect_items: bool = False,
) -> list[str]:
    """글자 노드 · 셀 · 문단 구분을 꺼낸다. `collect_items` 면 엑셀 `<si>` 별 문자열 목록."""
    xml = data.decode("utf-8", errors="replace")
    out: list[str] = []
    items: list[str] = []
    inside_text = 0
    cell_type = ""
    in_value = False
    value: list[str] = []
    for match in _TOKEN.finditer(xml):
        closing, tag, attributes, chars = match.groups()
        if chars is not None:
            if inside_text or every_text:
                piece = html.unescape(chars)
                out.append(piece)
                budget.chars -= len(piece)
                if budget.chars < 0:
                    raise _TooLarge
            elif in_value:
                value.append(chars)
            continue
        if tag is None:
            continue
        name = tag.lower()
        self_closing = attributes.endswith("/")
        if attributes and not closing:
            for first, second in _ATTRIBUTE_TEXT.findall(attributes):
                text = html.unescape(first or second)
                if text.strip():
                    out.append(f"\n{text}\n")
                    budget.chars -= len(text)
        if name in _TEXT_TAGS and not self_closing:
            inside_text += -1 if closing else 1
            inside_text = max(inside_text, 0)
        elif name == "c" and not closing:
            found = _CELL_TYPE.search(attributes)
            cell_type = found.group(1) if found else ""
        elif name == "v":
            if closing:
                in_value = False
                number = "".join(value).strip()
                if cell_type == "s" and shared is not None and number.isdigit():
                    index = int(number)
                    out.append(shared[index] if index < len(shared) else "")
                elif number:
                    out.append(number)
                value = []
            elif not self_closing:
                in_value, value = True, []
        if name in _LINE_BREAKS and (self_closing or not closing):
            out.append("\n")
        elif closing and name in _LINE_TAGS:
            out.append("\n")
            if collect_items and name == "si":
                items.append("".join(out).strip())
                out = []
        elif name in _CELL_TAGS and (closing or self_closing):
            out.append("\t")
    return items if collect_items else out


# ── PDF ─────────────────────────────────────────────────────────────────────


# PDF 를 읽는 자식 프로세스 — 표준입력으로 바이트를 받아 표준출력으로 `[글, 사유]` JSON 을 낸다.
_PDF_CHILD = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from garim.attachments import _read_pdf\n"
    "raw = sys.stdin.buffer.read()\n"
    "try:\n"
    "    result = _read_pdf(raw, int(sys.argv[2]))\n"
    "except Exception:\n"
    "    result = (None, 'unreadable')\n"
    "sys.stdout.write(json.dumps(result))\n"
)


def _pdf(raw: bytes, max_chars: int) -> Extraction:
    try:
        import pypdf  # noqa: F401 — 있는지만 본다. 읽기는 자식 프로세스에서 한다.
    except ImportError:
        return Extraction("pdf", None, "needs_docs_extra")
    # ★ pypdf 는 악의적 스트림(모든 쪽이 같은 큰 FlateDecode 를 가리키는 것)에 몇 시간을 쓸 수 있고
    #   스레드로는 멈출 수 없다. 새 파이썬에서 읽고 시간을 넘기면 죽인다. multiprocessing(spawn)은
    #   부모의 메인 스크립트를 다시 import 해서, `if __name__ == "__main__"` 없이 가림을 부르는
    #   스크립트에서 시작조차 못 했다(재검토).
    package_root = str(Path(__file__).resolve().parents[1])
    try:
        completed = subprocess.run(  # noqa: S603 — 고정된 인자, 셸 없음
            [sys.executable, "-c", _PDF_CHILD, package_root, str(max_chars)],
            input=raw,
            capture_output=True,
            timeout=PDF_TIMEOUT_SECONDS,
            check=False,
        )
        text, reason = json.loads(completed.stdout.decode("utf-8"))
    except subprocess.TimeoutExpired:
        return Extraction("pdf", None, "timeout")
    except (OSError, ValueError, UnicodeDecodeError, TypeError):
        return Extraction("pdf", None, "unreadable")
    if text is None:
        return Extraction("pdf", None, reason or "unreadable")
    return Extraction("pdf", text) if text.strip() else Extraction("pdf", None, "no_text")


def _read_pdf(raw: bytes, max_chars: int) -> tuple[str | None, str | None]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw[raw.find(b"%PDF-") :]))
    if reader.is_encrypted and not reader.decrypt(""):
        return None, "encrypted"
    if len(reader.pages) > MAX_PDF_PAGES:  # 쪽 수는 읽기 전에 본다
        return None, "too_large"
    pieces: list[str] = []
    size = 0
    for page in reader.pages:
        texts = [page.extract_text() or ""]
        texts.extend(_annotation_texts(page))  # 주석(FreeText · 메모)의 글
        for text in texts:
            pieces.append(text)
            size += len(text)
            if size > max_chars:
                return None, "too_large"
    try:  # 양식 필드 값(AcroForm) — 깨진 양식 하나로 문서 전체를 버리지 않는다
        fields = reader.get_fields() or {}
    except Exception:
        fields = {}
    for field in fields.values():
        value = field.get("/V") if hasattr(field, "get") else None
        if value:
            pieces.append(str(value))
    return "\n".join(pieces), None


def _annotation_texts(page) -> list[str]:  # noqa: ANN001 — pypdf 객체
    try:
        annotations = page.get("/Annots")
        annotations = annotations.get_object() if annotations is not None else []
        texts = []
        for annotation in annotations or []:
            contents = annotation.get_object().get("/Contents")
            if contents:
                texts.append(str(contents))
        return texts
    except Exception:
        return []


def _natural_key(name: str) -> list[object]:
    """`slide10` 이 `slide2` 뒤에 오게."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]
