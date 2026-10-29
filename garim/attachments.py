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
  주민번호를 놓치거나 카드 번호를 지어냈다.
★ 0.1.4 최종 검토 — 그 수정에서 또 새는 길이 나왔다. CDATA · 주석 안의 `</w:t>` 가 뒤의 글을 버렸고,
  `.xml` 이 아닌 이름에 둔 본문 · 앞에 바이트를 붙인 문서 · `[docs]` 없는 ASCII PDF 가 검사 없이
  나갔고, 탭 · 공유 문자열이 글자 상한에 세지지 않았고, PDF 자식 프로세스가 현재 디렉터리의 모듈을
  import 했다.
"""

from __future__ import annotations

import codecs
import html
import io
import json
import re
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
# 압축을 푼 XML 합계 · 멤버 수 · 꺼낸 글자 수 상한 — 압축 폭탄과 끝없는 문서를 막는다.
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 4_000
MAX_EXTRACTED_CHARS = 2_000_000
MAX_PDF_PAGES = 500
PDF_TIMEOUT_SECONDS = 20.0
# 오피스 · 한글 · ODF 문서 하나(내장 문서 포함)를 읽는 시간 — 넘기면 읽지 못한 문서(`timeout`)다.
OFFICE_TIMEOUT_SECONDS = 20.0
MAX_NESTING = 2
# 요청 하나에서 꺼낼 문서 수 — PDF 하나마다 새 파이썬을 띄우므로, 작은 PDF 수천 개로 요청 하나가
# 몇십 분을 쓰지 못하게 한다. 넘친 문서는 읽지 못한 문서(`too_many`)다.
MAX_DOCUMENTS_PER_REQUEST = 16

# 읽지 못한 문서 — 정책 `attachments.unreadable`(기본 block). 그 밖의 사유는 `uninspected`.
UNREADABLE_REASONS = frozenset(
    {"too_large", "too_many", "unreadable", "encrypted", "timeout", "needs_docs_extra"}
)
DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".pptx", ".xlsx", ".hwpx", ".odt", ".ods", ".odp"})

_EMBEDDED_DOCUMENT = re.compile(r"\.(?:docx|xlsx|pptx|hwpx|odt|ods|odp)$")
# 열지 못한 zip 이 문서였는가 — 로컬 헤더의 파트 이름은 압축되지 않는다.
_DOCUMENT_ZIP_NAMES = re.compile(
    rb"\[content_types\]\.xml|word/document|xl/workbook|ppt/presentation|contents/section|"
    rb"opendocument|hwp\+zip",
    re.I,
)
# PDF 머리(`%PDF-`)는 앞 1024바이트 안에 있어야 한다 — PDF 리더의 관례다. 그 뒤로 간접 객체
# (`12 0 obj`)가 있어야 PDF 다. 없으면 `%PDF-` 를 품은 텍스트다. 수량자를 묶어 선형이다.
_PDF_HEADER_WINDOW = 1024
_PDF_OBJECT = re.compile(
    rb"(?<![0-9])[0-9]{1,10}[ \t\r\n\f\x00]{1,16}[0-9]{1,5}[ \t\r\n\f\x00]{1,16}obj\b"
)
# 태그. 따옴표 안의 `>` 는 속성 값이다. 소유 수량자라 이름과 속성 사이를 되짚지 않고(★ 재검토:
# 이름 2만 자에 11초), 태그 안에 `<` 를 허락하지 않아 실패해도 다음 `<` 에서 멈춘다.
_TAG = re.compile(r"""<(/?)(?:[\w.-]++:)?([\w.-]++)((?:[^<>"']++|"[^"<]*+"|'[^'<]*+')*+)>""")
# DOCTYPE 안에서 건너뛸 것 — 따옴표, 주석, 내부 부분집합의 끝.
_DOCTYPE_MARK = re.compile(r"""["'\[\]>]|<!--|<\?""")
# 내부 일반 엔티티 선언과 참조 — 매개변수 엔티티(`%`) · 외부 엔티티(SYSTEM)는 풀지 않는다.
_ENTITY_DECL = re.compile(r"""<!ENTITY\s+([^\s%"'<>]+)\s+(?:"([^"]*)"|'([^']*)')""")
_ENTITY_REF = re.compile(r"&([^\s&;#<>]+);")
# XML 파트의 시작 — `<` 다음에 이름 · `?` · `!` 가 온다.
_XML_START = re.compile(r"<[A-Za-z_?!]")
# 문서의 본문 파트 — XML 로 읽히지 않으면 문서를 읽지 못한 것이다.
_MAIN_PART = re.compile(
    r"^(?:word/document\.xml|xl/workbook\.xml|xl/worksheets/[^/]+|ppt/presentation\.xml|"
    r"ppt/slides/[^/]+|content\.xml|contents/section\d*\.xml)$",
    re.I,
)
_SVG_ROOT = re.compile(r"<svg[\s>/]", re.I)
# 워드 altChunk 처럼 문서가 그대로 보여 주는 HTML · 글 파트.
_TEXT_PART = re.compile(r"\.(?:html?|xhtml|mht(?:ml)?|txt)$", re.I)
_ATTRIBUTE_TEXT = re.compile(
    r"""\b(?:descr|title|alt)\s*=\s*"([^"]*)"|\b(?:descr|title|alt)\s*=\s*'([^']*)'"""
)
_CELL_TYPE = re.compile(r"""\bt\s*=\s*["']([^"']*)["']""")
# `f` 는 엑셀 수식 — 캐시 값 없이 수식의 문자열 상수에만 값을 둘 수 있다.
_TEXT_TAGS = frozenset({"t", "deltext", "instrtext", "f"})
_LINE_TAGS = frozenset({"p", "tr", "row", "si", "h", "list-item", "table-row"})
_LINE_BREAKS = frozenset({"br", "cr", "linebreak", "line-break"})
_CELL_TAGS = frozenset({"tc", "c", "tab", "table-cell", "s"})


@dataclass(frozen=True, slots=True)
class Extraction:
    """꺼낸 글. `text` 가 None 이면 꺼내지 못했고 `reason` 이 이유다."""

    kind: str
    text: str | None
    reason: str | None = None


class _Shared:
    """내장 문서까지 한 문서 전체가 나눠 쓰는 상한 — 압축을 푼 바이트 · 마감 시각."""

    def __init__(self) -> None:
        self.bytes_left = MAX_UNCOMPRESSED_BYTES
        self.deadline = time.monotonic() + OFFICE_TIMEOUT_SECONDS

    def spend(self, size: int) -> None:
        self.bytes_left -= size
        if self.bytes_left < 0:
            raise _TooLarge
        if time.monotonic() > self.deadline:
            raise _Timeout


class _Budget:
    def __init__(self, chars: int, depth: int = 0, shared: _Shared | None = None) -> None:
        self.chars = chars
        self.depth = depth
        # ★ 재검토 — 내장 문서마다 64MB 를 새로 줘서, 내장 문서 4개짜리 1.9KB DOCX 가 40초를 썼다.
        self.shared = shared or _Shared()


class _TooLarge(Exception):
    pass


class _Timeout(Exception):
    pass


class _Malformed(Exception):
    """닫히지 않은 주석 · CDATA · 처리 지시 · DOCTYPE — 글을 다 읽었다고 말할 수 없는 XML."""


def _containers(raw: bytes) -> tuple[bool, int]:
    """(zip 인가, PDF 머리의 위치 또는 -1). 둘 다일 수 있다.

    ★ 재검토 — 앞에 1바이트를 붙인 DOCX 를 이진으로 보내서, zip 은 끝의 중앙 디렉터리로도 알아본다.
      PDF 머리는 앞 1KB 안만 본다 — 어디서든 찾던 판은 전화번호 뒤에 예제 PDF 를 붙인 텍스트를
      PDF 로 읽고 전화번호를 버렸다. PDF 뒤에 DOCX 를 붙인 것처럼 둘 다면 둘 다 읽는다.
    """
    try:
        is_zip = raw.startswith(b"PK\x03\x04") or zipfile.is_zipfile(io.BytesIO(raw))
    except (
        Exception
    ):  # ★ 재검토: 디스크 번호가 틀린 zip64 로케이터에 BadZipFile 이 새어 500 이 났다
        is_zip = False
    start = raw.find(b"%PDF-", 0, _PDF_HEADER_WINDOW + 5)
    if start >= 0 and _PDF_OBJECT.search(raw, start) is None:
        start = -1
    return is_zip, start


def document_kind(raw: bytes) -> str | None:
    """글을 꺼내지 않고 문서인지만 본다(`pdf` · `docx` · …). 문서가 아니면 None."""
    is_zip, pdf_at = _containers(raw)
    if is_zip:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = {info.filename.lower(): info.filename for info in archive.infolist()}
                kind = _zip_kind(names, archive)
        except Exception:
            kind = "document" if _DOCUMENT_ZIP_NAMES.search(raw) else None
        if kind is not None:
            return kind
    return "pdf" if pdf_at >= 0 else None


def extract_document(raw: bytes, *, max_chars: int = MAX_EXTRACTED_CHARS) -> Extraction | None:
    """문서 형식이면 글을 꺼낸다. 문서가 아니면(이미지 · 오디오 · 모르는 이진 · 보통 zip) None.

    여러 형식으로 읽히면(PDF 뒤에 붙인 DOCX, PDF 머리 앞의 글) 전부 읽고, 하나라도 읽지 못하면
    문서 전체를 읽지 못한 것으로 본다.
    """
    is_zip, pdf_at = _containers(raw)
    if not is_zip and pdf_at < 0:
        return _svg(raw, max_chars)
    if len(raw) > MAX_DOCUMENT_BYTES:
        return Extraction("document", None, "too_large")
    found: list[Extraction] = []
    if is_zip:
        office = _zip_document(raw, _Budget(max_chars))
        if office is not None:
            found.append(office)
            # zip 앞 · 끝 레코드 뒤의 글 — ★ 재검토: 주민번호 한 줄 뒤에 DOCX 를 붙인 텍스트
            # 첨부에서 DOCX 만 읽었다.
            start, end = raw.find(b"PK\x03\x04"), raw.rfind(b"PK\x05\x06")
            for outside in (raw[: max(start, 0)], raw[end + 22 :] if end >= 0 else b""):
                text = _plain_text(outside)
                if text:
                    found.append(Extraction(office.kind, text))
    if pdf_at >= 0:
        found.append(_pdf(raw, max_chars))
        prefix = _plain_text(raw[:pdf_at])
        if prefix:
            found.append(Extraction("pdf", prefix))
    if not found:
        return None
    if sum(len(item.text or "") for item in found) > max_chars:
        return Extraction(found[0].kind, None, "too_large")
    for item in found:
        if item.text is None and item.reason in UNREADABLE_REASONS:
            return item
    texts = [item.text for item in found if item.text]
    if not texts:
        return found[0]
    return Extraction(found[0].kind, "\n".join(texts))


def _svg(raw: bytes, max_chars: int) -> Extraction | None:
    """SVG 그림이면 보이는 글(`<text>` · 제목)만 꺼낸다. SVG 가 아니면 None.

    ★ 재검토 — SVG data URL 을 텍스트 첨부로 보고 가려 다시 인코딩하면서, 경로 좌표(`1.5.5.5`)를
      IP 주소로 가려 그림을 깨뜨렸다. 좌표는 속성에 있으므로 글자 노드만 읽고, 가리지 않는다.
    """
    head = raw[:4096]
    encoding = _xml_encoding(head)
    if encoding is None:
        return None
    text = head.decode(encoding, errors="replace").lstrip("\ufeff \t\r\n")
    if not text.startswith("<") or _SVG_ROOT.search(text) is None:
        return None
    if len(raw) > MAX_DOCUMENT_BYTES:
        return Extraction("svg", None, "too_large")
    try:
        pieces = _xml_text(raw, _Budget(max_chars), every_text=True)
    except _TooLarge:
        return Extraction("svg", None, "too_large")
    except _Timeout:
        return Extraction("svg", None, "timeout")
    except Exception:
        return Extraction("svg", None, "unreadable")
    return Extraction("svg", _tidy("".join(pieces)))  # 글이 없는 그림도 다 읽은 그림이다


def _plain_text(data: bytes) -> str | None:
    """사람이 읽는 글이면 그 글 — UTF-8 · BOM 붙은 UTF-16 · CP949, NUL 없이 거의 다 인쇄 가능."""
    if not data.strip():
        return None
    encodings = ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8", "cp949")
    for encoding in encodings:
        try:
            text = data.decode(encoding).strip("\ufeff \t\r\n")
        except UnicodeDecodeError:
            continue
        printable = sum(c.isprintable() or c in "\r\n\t" for c in text)
        if text and "\x00" not in text and printable >= 0.95 * len(text):
            return text
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
            # 앞만 읽는다 — 통째로 읽으면 압축 폭탄 하나가 상한 밖에서 수 GB 를 푼다(재검토).
            with archive.open(names["mimetype"]) as stream:
                mimetype = stream.read(100).decode("ascii", errors="replace")
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
        # 파트 이름이 문서의 것이면 깨진 문서다 — 이진으로 보내지 않는다.
        if budget.depth == 0 and not _DOCUMENT_ZIP_NAMES.search(raw):
            return None
        return Extraction("document", None, "unreadable")
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            return Extraction("document", None, "too_large")
        names = {info.filename.lower(): info.filename for info in infos}
        kind = _zip_kind(names, archive)
        if kind is None:
            return None  # 문서가 아닌 zip — 첨부로 건너뛴다
        try:
            # ★ 재검토 — 이름이 `.xml` 인 파트만 읽어서, 관계(`_rels`)로 가리킨 `word/body.bin` ·
            #   `word/settings.xml` 의 본문을 놓쳤다. 이름이 아니라 내용(XML 인가)으로 고른다.
            members = [info for info in infos if not info.is_dir()]
            embedded = [
                info for info in members if _EMBEDDED_DOCUMENT.search(info.filename.lower())
            ]
            parts, texts = [], []
            for info in members:
                if info in embedded:
                    continue
                if _is_xml(archive, info, budget.shared):
                    parts.append(info)
                elif _MAIN_PART.match(info.filename):
                    raise _Malformed  # 본문 파트가 XML 로 읽히지 않는다 — 다 읽었다고 할 수 없다
                elif _TEXT_PART.search(info.filename):
                    texts.append(info)
            budget.shared.spend(sum(info.file_size for info in parts + texts + embedded))
            # 엑셀 공유 문자열을 먼저 읽는다 — 셀은 번호로만 가리킨다.
            shared: list[str] = []
            for info in parts:
                if info.filename.lower().endswith("sharedstrings.xml"):
                    shared = _xml_text(_read(archive, info), budget, collect_items=True)
            # 셀이 가리키지 않는 공유 문자열에도 값을 숨길 수 있다 — 전부 검사한다. 셀은 아래에서
            # 행 순서대로 한 번 더 나온다(행 문맥).
            pieces: list[str] = [item for text in shared for item in (text, "\n")]
            for info in sorted(parts, key=lambda item: _natural_key(item.filename)):
                if info.filename.lower().endswith("sharedstrings.xml"):
                    continue
                lowered = info.filename.lower()
                every_text = (
                    kind == "odf" or "docprops/" in lowered or bool(_TEXT_PART.search(lowered))
                )
                pieces.extend(_xml_text(_read(archive, info), budget, shared, every_text))
                pieces.append("\n")
            for info in texts:  # 글 파트(altChunk `.txt` 등)
                text = _plain_text(_read(archive, info))
                if text:
                    budget.chars -= len(text)
                    if budget.chars < 0:
                        raise _TooLarge
                    pieces.extend((text, "\n"))
            for info in embedded:
                if budget.depth + 1 >= MAX_NESTING:
                    return Extraction(kind, None, "too_large")
                inner = _zip_document(
                    _read(archive, info), _Budget(budget.chars, budget.depth + 1, budget.shared)
                )
                if inner is not None:
                    if inner.text is None:
                        if inner.reason == "no_text":
                            continue  # ★ 재검토: 빈 내장 문서 하나로 바깥 문서의 글을 버렸다
                        return Extraction(kind, None, inner.reason)
                    budget.chars -= len(inner.text)
                    if budget.chars < 0:
                        raise _TooLarge
                    pieces.extend((inner.text, "\n"))
        except _TooLarge:
            return Extraction(kind, None, "too_large")
        except _Timeout:
            return Extraction(kind, None, "timeout")
        except Exception:
            # 깨진 멤버(CRC) · 암호 · 지원하지 않는 압축 · 닫히지 않은 주석 — 일부만 읽은 문서는
            # 믿지 않는다
            return Extraction(kind, None, "unreadable")
    text = _tidy("".join(pieces))
    return Extraction(kind, text) if text else Extraction(kind, None, "no_text")


def _tidy(text: str) -> str:
    """줄 끝 · 줄 머리의 공백 · 탭과 빈 줄을 지운다.

    ★ 재검토 — `[ \\t]*\\n` 정규식은 긴 공백 줄에서 제곱 시간이었다(1.6만 칸에 0.36초, 길이가
      두 배면 네 배). 줄 단위로 잘라 선형으로 한다.
    """
    lines = (line.strip(" \t") for line in text.split("\n"))
    return "\n".join(line for line in lines if line).strip()


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    return archive.read(info)


def _xml_encoding(head: bytes) -> str | None:
    """XML 파트의 인코딩 — BOM, 또는 ASCII 마크업이 만드는 NUL 무늬(BOM 없는 UTF-16).

    이진이면 None.
    """
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if b"\x00" not in head:
        return "utf-8"
    even, odd = head[0::2], head[1::2]
    if even.count(0) > len(even) * 0.4 and odd.count(0) < len(odd) * 0.1:
        return "utf-16-be"
    if odd.count(0) > len(odd) * 0.4 and even.count(0) < len(even) * 0.1:
        return "utf-16-le"
    return None


def _is_xml(archive: zipfile.ZipFile, info: zipfile.ZipInfo, shared: _Shared) -> bool:
    """앞부분으로 XML 파트인지 본다 — 그림 · 글꼴 · 내장 OLE 는 읽지 않는다.

    공백으로 시작하는 XML 은 공백을 끝까지 넘겨 본다(★ 재검토: 앞 64바이트만 · 4KB 만 보던 판은
    공백 뒤의 본문을 건너뛰었다). 넘겨 읽은 양은 문서의 압축 해제 상한에 센다. 첫 글자가 우연히
    `<` 인 글꼴 같은 이진은 깨진 글자 · NUL 로 거른다.
    """
    with archive.open(info) as stream:
        head = stream.read(4096)
        shared.spend(len(head))
        encoding = _xml_encoding(head)
        if encoding is None:
            return False
        # 증분 디코더 — 청크 경계에서 UTF-8 글자 · UTF-16 짝이 잘려도 이어 읽는다.
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        text = decoder.decode(head).lstrip("\ufeff \t\r\n")
        while len(text) < 2:
            chunk = stream.read(65536)
            shared.spend(len(chunk))
            text = (text + decoder.decode(chunk, final=not chunk)).lstrip("\ufeff \t\r\n")
            if not chunk:
                break
    if not _XML_START.match(text):
        return False
    sample = text[:1024]
    return "\x00" not in sample and sample.count("\ufffd") <= len(sample) // 10


def _decode_xml(data: bytes) -> str:
    return data.decode(_xml_encoding(data[:4096]) or "utf-8", errors="replace")


def _tokens(xml: str) -> Iterator[tuple[str | None, str | None, str, str | None, str]]:
    """(닫힘 `/`, 태그, 속성, 글자, 글자의 종류 `text` · `cdata` · `dtd`).

    주석 · 처리 지시는 건너뛰고 CDATA 는 글자다. DOCTYPE 의 내부 부분집합은 `dtd` 글자로 내놓는다 —
    엔티티 값(`<!ENTITY n "…">`)에 숨긴 글도 탐지에 닿는다. 닫히지 않은 주석 · CDATA · 처리 지시 ·
    DOCTYPE 는 `_Malformed` 다. 닫는 표시를 한 번씩만 찾으므로 선형이다.

    ★ 재검토 — 정규식 하나로 토큰을 끊던 판은 CDATA · 주석 · 처리 지시 안의 `</w:t>` 를 진짜 닫는
      태그로 읽어 뒤의 글을 버렸다(`<w:t><![CDATA[</w:t>]]>주민번호…</w:t>`). DOCTYPE 를 통째로
      거부하던 판은 SVG 그림 · LibreOffice 매크로가 든 평범한 문서를 막았다.
    """
    position, end = 0, len(xml)
    while position < end:
        start = xml.find("<", position)
        if start < 0:
            yield None, None, "", xml[position:], "text"
            return
        if start > position:
            yield None, None, "", xml[position:start], "text"
        for opener, closer, keep in (
            ("<!--", "-->", False),
            ("<![CDATA[", "]]>", True),
            ("<?", "?>", False),
        ):
            if xml.startswith(opener, start):
                close = xml.find(closer, start + len(opener))
                if close < 0:
                    raise _Malformed
                if keep:
                    yield None, None, "", xml[start + len(opener) : close], "cdata"
                position = close + len(closer)
                break
        else:
            if xml.startswith("<!DOCTYPE", start):
                position, subset = _doctype(xml, start + len("<!DOCTYPE"))
                if subset.strip():
                    yield None, None, "", subset, "dtd"
                continue
            match = _TAG.match(xml, start)
            if match is None:
                position = start + 1  # 짝 없는 `<` — 버린다
            else:
                yield match.group(1), match.group(2), match.group(3), None, "text"
                position = match.end()


def _doctype(xml: str, position: int) -> tuple[int, str]:
    """DOCTYPE 의 끝 위치와 내부 부분집합. 따옴표 · 주석 안의 `>` · `]` 는 건너뛴다."""
    subset_start = -1
    while (mark := _DOCTYPE_MARK.search(xml, position)) is not None:
        found = mark.group()
        if found in "\"'":
            close = xml.find(found, mark.end())
            if close < 0:
                raise _Malformed
            position = close + 1
        elif found in ("<!--", "<?"):
            closer = "-->" if found == "<!--" else "?>"
            close = xml.find(closer, mark.end())
            if close < 0:
                raise _Malformed
            position = close + len(closer)
        elif found == "[" and subset_start < 0:
            subset_start = position = mark.end()
        elif found == "]" and subset_start >= 0:
            close = xml.find(">", mark.end())
            if close < 0:
                raise _Malformed
            return close + 1, xml[subset_start : mark.start()]
        elif found == ">" and subset_start < 0:
            return mark.end(), ""
        else:
            position = mark.end()
    raise _Malformed


def _xml_text(
    data: bytes,
    budget: _Budget,
    shared: list[str] | None = None,
    every_text: bool = False,
    collect_items: bool = False,
) -> list[str]:
    """글자 노드 · 셀 · 문단 구분을 꺼낸다. `collect_items` 면 엑셀 `<si>` 별 문자열 목록.

    내놓는 글은 탭 · 줄바꿈 · 공유 문자열 참조까지 전부 글자 상한에 센다 — ★ 재검토: 셀이 가리키는
    공유 문자열과 `<v>` 값을 세지 않아 888바이트 엑셀이 상한 1만 자에 500만 자를 냈다.
    """
    xml = _decode_xml(data)
    out: list[str] = []
    items: list[str] = []
    inside_text = 0
    cell_type = ""
    in_value = False
    value: list[str] = []

    def emit(piece: str) -> None:
        out.append(piece)
        budget.chars -= len(piece)
        if budget.chars < 0:
            raise _TooLarge

    entities: dict[str, str] = {}

    def expand(chars: str) -> str:
        # DOCTYPE 의 엔티티를 한 단계만 푼다 — ★ 재검토: `&a;&b;` 로 나눠 쓴 주민번호를 놓쳤다.
        # 늘어나는 길이를 글자 상한에 견줘 엔티티 폭탄을 막는다.
        grown = 0

        def substitute(match: re.Match[str]) -> str:
            nonlocal grown
            value = entities.get(match.group(1))
            if value is None:
                return match.group(0)
            grown += len(value)
            if grown > budget.chars:
                raise _TooLarge
            return value

        return _ENTITY_REF.sub(substitute, chars)

    def separate(mark: str) -> None:
        # 구분자는 이어 쓰지 않는다 — ★ 재검토: 서식만 있는 빈 셀 10만 개의 탭이 상한을 넘겨
        # 평범한 엑셀이 막혔다. 줄바꿈이 탭보다 세다.
        if not out:
            return
        if out[-1] in ("\t", "\n", " "):
            if mark == "\n":
                out[-1] = "\n"
            return
        emit(mark)

    for index, (closing, tag, attributes, chars, kind) in enumerate(_tokens(xml)):
        if index % 4096 == 0 and time.monotonic() > budget.shared.deadline:
            raise _Timeout
        if chars is not None:
            if kind == "text" and entities:
                chars = expand(chars)
            piece = chars if kind != "text" else html.unescape(chars)
            if kind == "dtd":
                for name, double, single in _ENTITY_DECL.findall(piece):
                    entities.setdefault(name, double or single)
                emit(f"\n{piece}\n")
            elif inside_text or every_text:
                if piece.strip():
                    emit(piece)
                else:  # 들여쓰기 같은 공백 덩어리는 구분자 하나다
                    separate("\n" if "\n" in piece else " ")
            elif in_value:
                value.append(piece)  # 엔티티로 쓴 숫자(`&#57;`)도 값이다(재검토)
            continue
        name = (tag or "").lower()
        self_closing = attributes.rstrip().endswith("/")
        if attributes and not closing:
            for first, second in _ATTRIBUTE_TEXT.findall(attributes):
                text = html.unescape(first or second)
                if text.strip():
                    emit(f"\n{text}\n")
        if name in _TEXT_TAGS and not self_closing:
            inside_text += -1 if closing else 1
            inside_text = max(inside_text, 0)
            if closing and name == "f":
                separate("\n")  # ★ 재검토: 수식 글이 셀 값에 붙어 `C1900101-…` 가 됐다
        elif name == "c" and not closing:
            found = _CELL_TYPE.search(attributes)
            cell_type = found.group(1) if found else ""
        elif name == "v":
            if closing:
                in_value = False
                number = "".join(value).strip()
                if cell_type == "s" and shared is not None and number.isdigit():
                    index = int(number)
                    emit(shared[index] if index < len(shared) else "")
                elif number:
                    emit(number)
                value = []
            elif not self_closing:
                in_value, value = True, []
        if name in _LINE_BREAKS and (self_closing or not closing):
            separate("\n")
        elif closing and name in _LINE_TAGS:
            separate("\n")
            if collect_items and name == "si":
                items.append("".join(out).strip())
                out.clear()
        elif name in _CELL_TAGS and (closing or self_closing):
            separate("\t")
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
    # ★ 최종 검토 — `python -c` 는 현재 디렉터리를 import 경로 맨 앞에 둔다. 검사하는 디렉터리에
    #   `json.py` 를 심으면 `garim scan .` 이 그 코드를 돌렸다. `-P` 로 그 경로를 뺀다.
    package_root = str(Path(__file__).resolve().parents[1])
    try:
        completed = subprocess.run(  # noqa: S603 — 고정된 인자, 셸 없음
            [sys.executable, "-P", "-c", _PDF_CHILD, package_root, str(max_chars)],
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
