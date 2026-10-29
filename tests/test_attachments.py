"""첨부 문서 검사 — 프록시 · scan 이 PDF · DOCX · XLSX · PPTX · HWPX 안의 글을 본다(0.1.4)."""

from __future__ import annotations

import base64
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from garim import attachments
from garim.attachments import extract_document
from garim.proxy import ProxyConfig, ProxyService


def _rrn() -> str:
    digits = "900101123456"
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    check = (11 - sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11) % 10
    return f"{digits[:6]}-{digits[6:]}{check}"


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    return _zip(
        {"[Content_Types].xml": "<Types/>", "word/document.xml": f"<w:document>{body}</w:document>"}
    )


def _pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 144] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 600


# ── 글 꺼내기 ────────────────────────────────────────────────────────────────


def test_office_and_hwpx_documents_yield_their_text() -> None:
    assert (
        extract_document(_docx("담당자 연락처", "010-1234-5678")).text
        == "담당자 연락처\n010-1234-5678"
    )
    pptx = _zip(
        {"ppt/presentation.xml": "<p/>", "ppt/slides/slide1.xml": "<a:p><a:t>대외비</a:t></a:p>"}
    )
    xlsx = _zip(
        {
            "xl/workbook.xml": "<w/>",
            "xl/sharedStrings.xml": "<sst><si><t>계좌</t></si><si><t>110-123-456789</t></si></sst>",
        }
    )
    hwpx = _zip(
        {
            "Contents/content.hpf": "<h/>",
            "Contents/section0.xml": "<hp:p><hp:t>주민번호</hp:t></hp:p>",
        }
    )
    assert extract_document(pptx).text == "대외비"
    assert extract_document(xlsx).text == "계좌\n110-123-456789"
    assert extract_document(hwpx).text == "주민번호"


def test_xml_entities_are_unescaped_and_unknown_zips_are_not_documents() -> None:
    assert extract_document(_docx("a &amp; b &lt;c&gt;")).text == "a & b <c>"
    assert extract_document(_zip({"readme.txt": "hello"})) is None
    assert extract_document(_png()) is None


def test_oversized_documents_are_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """압축 폭탄 — 압축을 푼 크기를 읽기 전에 본다."""
    monkeypatch.setattr(attachments, "MAX_UNCOMPRESSED_BYTES", 100)
    result = extract_document(_docx("가" * 500))
    assert result is not None and result.text is None and result.reason == "too_large"


def test_pdf_text_needs_the_docs_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pypdf")
    assert "010-1234-5678" in (extract_document(_pdf("Tel 010-1234-5678")).text or "")
    monkeypatch.setitem(sys.modules, "pypdf", None)  # 설치되지 않은 것처럼
    result = extract_document(_pdf("Tel 010-1234-5678"))
    assert result is not None and result.text is None and result.reason == "needs_docs_extra"


# ── 프록시 ──────────────────────────────────────────────────────────────────


class _Upstream:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, path: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        self.calls.append(payload)
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


def _proxy(upstream: _Upstream, policy: str | None = None) -> ProxyService:
    config = ProxyConfig(upstream_base_url="https://upstream.test/v1", policy_path=policy)
    return ProxyService(config, sender=upstream)


def _request(data: bytes, mime: str, part: str = "file") -> dict[str, Any]:
    url = f"data:{mime};base64," + base64.b64encode(data).decode()
    attachment = (
        {"type": "file", "file": {"file_data": url, "filename": "a"}}
        if part == "file"
        else {"type": "image_url", "image_url": {"url": url}}
    )
    return {
        "model": "x",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "요약해줘"}, attachment]}
        ],
    }


def _policy(tmp_path: Path, **attachment: str) -> str:
    path = tmp_path / "policy.yaml"
    path.write_text(json.dumps({"version": 1, "attachments": attachment}), encoding="utf-8")
    return str(path)


def test_a_document_with_a_maskable_value_is_blocked_by_default() -> None:
    """★ 0.1.3 까지 이진 첨부는 통째로 건너뛰어 문서 속 번호가 그대로 나갔다."""
    upstream = _Upstream()
    request = _request(
        _docx("담당자 010-1234-5678"), "application/vnd.openxmlformats-officedocument"
    )
    status, body, headers = _proxy(upstream).handle_chat_completions(request, {})
    assert status == 400 and upstream.calls == []
    assert "attachment_unmaskable" in body["error"]["degraded_reasons"]
    assert body["error"]["findings"][0]["code"] == "L-CM-02"
    assert body["error"]["findings"][0]["message"] == 0
    assert headers["X-Garim-Verdict"] == "block"


def test_unmaskable_values_can_be_forwarded_with_a_warning(tmp_path: Path) -> None:
    upstream = _Upstream()
    request = _request(_docx("담당자 010-1234-5678"), "application/octet-stream")
    status, _, headers = _proxy(
        upstream, _policy(tmp_path, unmaskable="warn")
    ).handle_chat_completions(request, {})
    assert status == 200 and headers["X-Garim-Verdict"] == "warn"
    assert upstream.calls[0]["messages"][0]["content"][1] == request["messages"][0]["content"][1]


def test_block_level_values_in_documents_block_regardless_of_policy(tmp_path: Path) -> None:
    upstream = _Upstream()
    request = _request(_docx(f"주민번호 {_rrn()}"), "application/octet-stream")
    status, _, _ = _proxy(upstream, _policy(tmp_path, unmaskable="warn")).handle_chat_completions(
        request, {}
    )
    assert status == 400 and upstream.calls == []


def test_a_clean_document_and_an_image_pass_under_the_default_policy() -> None:
    upstream = _Upstream()
    status, _, headers = _proxy(upstream).handle_chat_completions(
        _request(_docx("회의 일정 공유드립니다"), "application/octet-stream"), {}
    )
    assert status == 200 and headers["X-Garim-Verdict"] == "allow"
    status, _, headers = _proxy(upstream).handle_chat_completions(
        _request(_png(), "image/png", part="image"), {}
    )
    assert status == 200 and "X-Garim-Degraded-Reasons" not in headers


@pytest.mark.parametrize(("setting", "status"), [("warn", 200), ("block", 400)])
def test_uninspected_attachments_follow_the_policy(
    tmp_path: Path, setting: str, status: int
) -> None:
    upstream = _Upstream()
    result_status, _, headers = _proxy(
        upstream, _policy(tmp_path, uninspected=setting)
    ).handle_chat_completions(_request(_png(), "image/png", part="image"), {})
    assert result_status == status
    assert "attachment_not_inspected" in headers["X-Garim-Degraded-Reasons"]


def test_strict_profile_warns_on_images() -> None:
    upstream = _Upstream()
    status, _, headers = _proxy(upstream, "strict").handle_chat_completions(
        _request(_png(), "image/png", part="image"), {}
    )
    assert status == 200 and headers["X-Garim-Verdict"] == "warn"


def test_an_ascii_pdf_is_read_as_a_document_not_rewritten_as_text() -> None:
    """ASCII 로만 된 PDF 를 텍스트로 보고 가린 뒤 다시 인코딩하면 파일이 깨진다."""
    pytest.importorskip("pypdf")
    upstream = _Upstream()
    request = _request(_pdf("Contact kim@example.com"), "application/pdf")
    status, body, _ = _proxy(upstream).handle_chat_completions(request, {})
    assert status == 400 and "attachment_unmaskable" in body["error"]["degraded_reasons"]


# ── garim scan ──────────────────────────────────────────────────────────────


def test_scan_reads_documents_and_reports_unreadable_ones(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from garim.cli import main

    (tmp_path / "hr.docx").write_bytes(_docx(f"주민번호 {_rrn()}"))
    assert main(["scan", str(tmp_path)]) == 2
    assert "hr.docx: block" in capsys.readouterr().out
    (tmp_path / "hr.docx").unlink()
    (tmp_path / "broken.xlsx").write_bytes(
        b"PK\x03\x04\xff\xfe\x00 broken"
    )  # 문서도 텍스트도 아니다
    (tmp_path / "ok.txt").write_text("안녕하세요\n", encoding="utf-8")
    assert main(["scan", str(tmp_path)]) == 3
    assert "broken.xlsx" in capsys.readouterr().err


# ── 재검토(2026-10-29)에서 찾은 결함 ──────────────────────────────────────────


def _proxy_status(request: dict[str, Any], policy: str | None = None) -> tuple[int, dict[str, Any]]:
    upstream = _Upstream()
    status, body, headers = _proxy(upstream, policy).handle_chat_completions(request, {})
    return status, headers


def test_oversized_or_partly_broken_documents_fail_closed() -> None:
    """★ 일부러 크게 · 깨지게 만든 문서가 "검사 못 함 → 허용" 으로 주민번호를 내보냈다."""
    padded = _docx(f"주민번호 {_rrn()}", "가" * 2_100_000)
    status, headers = _proxy_status(_request(padded, "application/octet-stream"))
    assert status == 400 and "attachment_unreadable" in headers["X-Garim-Degraded-Reasons"]
    buffer = io.BytesIO(_docx(f"주민번호 {_rrn()}"))
    broken = bytearray(
        _zip({"word/document.xml": "<w:t>본문</w:t>", "word/header9.xml": "<w:t>x</w:t>"})
    )
    data_start = broken.find(b"word/header9.xml") + len(b"word/header9.xml")
    broken[data_start + 1] ^= 0xFF  # 멤버의 압축 데이터를 깬다(CRC 불일치 · 압축 오류)
    result = extract_document(bytes(broken))
    assert result is not None and result.reason == "unreadable"
    assert buffer  # 같은 문서라도 깨진 멤버가 있으면 믿지 않는다


def test_a_document_without_text_is_uninspected_not_clean(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    image_only = _pdf("").replace(b"BT /F1 12 Tf 10 100 Td () Tj ET", b"q Q" + b" " * 27)
    status, headers = _proxy_status(
        _request(image_only, "application/pdf"), _policy(tmp_path, uninspected="block")
    )
    assert status == 400 and "attachment_not_inspected" in headers["X-Garim-Degraded-Reasons"]


def test_a_warn_level_finding_does_not_cancel_unmaskable_block() -> None:
    """★ 같은 문서의 기밀 표식(warn) 하나가 연락처를 그대로 보내게 했다."""
    status, headers = _proxy_status(
        _request(_docx("대외비", "연락처 010-1234-5678"), "application/x")
    )
    assert status == 400 and "attachment_unmaskable" in headers["X-Garim-Degraded-Reasons"]


def test_text_that_looks_like_a_pdf_is_still_inspected() -> None:
    """★ `%PDF` 로 시작하는 텍스트 첨부가 문서 판정에 걸려 검사 없이 나갔다."""
    memo = f"%PDF 메모\n주민번호 {_rrn()}".encode()
    assert _proxy_status(_request(memo, "text/plain"))[0] == 400


def test_spreadsheet_cells_and_hwpx_inline_tags_are_separated() -> None:
    xlsx = _zip(
        {
            "xl/workbook.xml": "<w/>",
            "xl/sharedStrings.xml": "<sst><si><t>연락처</t></si></sst>",
            "xl/worksheets/sheet1.xml": (
                '<row><c t="s"><v>0</v></c><c><v>12</v></c>'
                '<c t="inlineStr"><is><t>010-1234-5678</t></is></c></row>'
            ),
        }
    )
    text = extract_document(xlsx).text or ""
    assert "12\t010-1234-5678" in text and "연락처" in text
    hwpx = _zip(
        {
            "Contents/content.hpf": "<h/>",
            "Contents/section0.xml": f'<hp:t>주민번호<hp:tab width="4000"/>{_rrn()}</hp:t>',
        }
    )
    assert extract_document(hwpx).text == f"주민번호\t{_rrn()}"


def test_text_node_scanning_is_linear() -> None:
    """★ `<w:t ` 를 이어 붙인 80KB XML 에 24초 — 글자 노드 정규식이 제곱이었다."""
    import time

    bomb = _zip({"word/document.xml": "<w:t " * 40_000})
    started = time.perf_counter()
    extract_document(bomb)
    assert time.perf_counter() - started < 2


@pytest.mark.parametrize(
    "part",
    [
        {"type": "input_file", "file_data": "DATA:application/octet-stream;BASE64,{b64}"},
        {"type": "image_url", "image_url": "data:application/octet-stream;base64,{b64}"},
    ],
)
def test_other_attachment_shapes_are_inspected(part: dict[str, Any]) -> None:
    """★ 고정된 세 자리만 봐서 `input_file` · 문자열 `image_url` · 대문자 스킴이 비껴갔다."""
    b64 = base64.b64encode(_docx(f"주민번호 {_rrn()}")).decode()
    filled = {
        key: value.format(b64=b64) if isinstance(value, str) else value
        for key, value in part.items()
    }
    request = {"model": "x", "messages": [{"role": "user", "content": [filled]}]}
    assert _proxy_status(request)[0] == 400


def test_file_references_and_short_plaintext_follow_the_right_rule(tmp_path: Path) -> None:
    policy = _policy(tmp_path, uninspected="block")
    reference = {
        "model": "x",
        "messages": [
            {"role": "user", "content": [{"type": "file", "file": {"file_id": "file-abc123"}}]}
        ],
    }
    assert _proxy_status(reference, policy)[0] == 400
    short = {
        "model": "x",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "file", "file": {"file_data": "4111111111111111"}}],
            }
        ],
    }
    status, headers = _proxy_status(short, policy)
    assert (
        status == 200 and headers["X-Garim-Verdict"] == "mask"
    )  # 원문으로 검사하고 첨부로 세지 않는다


def test_scan_treats_maskable_document_values_as_unmaskable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from garim.cli import main

    (tmp_path / "contacts.docx").write_bytes(_docx("연락처 010-1234-5678"))
    assert main(["scan", str(tmp_path)]) == 2
    assert "unmaskable document" in capsys.readouterr().err


# ── 최종 검토(0.1.4 뒤)에서 찾은 결함 ─────────────────────────────────────────


def test_the_pdf_child_does_not_import_from_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ `python -c` 가 현재 디렉터리를 import 경로 맨 앞에 둬서, 검사하는 곳의 `json.py` 가
    돌았다."""
    pytest.importorskip("pypdf")
    marker = tmp_path / "ran"
    (tmp_path / "json.py").write_text(
        f"open({str(marker)!r}, 'w').close()\nraise SystemExit(0)\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    result = extract_document(_pdf("Tel 010-1234-5678"))
    assert not marker.exists()
    assert result is not None and "010-1234-5678" in (result.text or "")


@pytest.mark.parametrize(
    "decoy",
    ["<![CDATA[</w:t>]]>", "<!-- </w:t> -->", "<?x </w:t> ?>"],
)
def test_markup_inside_cdata_comments_and_instructions_does_not_end_text(decoy: str) -> None:
    """★ CDATA · 주석 · 처리 지시 안의 `</w:t>` 를 닫는 태그로 읽어 뒤의 주민번호를 버렸다."""
    document = _zip(
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": f"<w:document><w:p><w:r><w:t>{decoy}주민번호 {_rrn()}</w:t>"
            "</w:r></w:p></w:document>",
        }
    )
    assert _rrn() in (extract_document(document).text or "")


def test_unclosed_markup_is_unreadable_and_doctype_entities_are_read() -> None:
    """닫히지 않은 주석 · DOCTYPE 는 읽지 못한 문서다. DOCTYPE 의 엔티티 값에 숨긴 글은 읽는다."""
    for xml in ("<w:document><w:t>앞</w:t><!-- 닫히지 않음", '<!DOCTYPE d [<!ENTITY n "x">'):
        result = extract_document(
            _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": xml})
        )
        assert result is not None and result.reason == "unreadable"
    hidden = f'<!DOCTYPE d [<!ENTITY n "{_rrn()}">]><w:document><w:t>&n;</w:t></w:document>'
    document = _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": hidden})
    assert _rrn() in (extract_document(document).text or "")


def test_ordinary_parts_with_a_doctype_do_not_block_the_document() -> None:
    """★ DOCTYPE 를 통째로 거부해서 SVG 그림 · LibreOffice 매크로가 든 평범한 문서가 막혔다."""
    svg = (
        '<?xml version="1.0"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" [<!ENTITY ns "http://x">]>'
        "<svg><text>그림</text></svg>"
    )
    docx = _zip(
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": "<w:document><w:t>회의록</w:t></w:document>",
            "word/media/image1.svg": svg,
        }
    )
    assert "회의록" in (extract_document(docx).text or "")
    odt = _zip(
        {
            "mimetype": "application/vnd.oasis.opendocument.text",
            "content.xml": "<office:document-content><text:p>회의록</text:p>"
            "</office:document-content>",
            "Basic/Standard/Module1.xml": '<?xml version="1.0"?>\n<!DOCTYPE script:module PUBLIC '
            '"-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">'
            "<script:module>Sub Main\nEnd Sub</script:module>",
        }
    )
    assert "회의록" in (extract_document(odt).text or "")


def test_body_parts_are_found_by_content_not_by_name() -> None:
    """★ `.xml` 이름의 파트만 읽어, 관계로 가리킨 `word/body.bin` · `word/settings.xml` 본문을
    놓쳤다."""
    body = f"<w:document><w:p><w:r><w:t>주민번호 {_rrn()}</w:t></w:r></w:p></w:document>"
    for name in ("word/body.bin", "word/settings.xml"):
        document = _zip(
            {"[Content_Types].xml": "<Types/>", name: body, "word/media/a.png": "\x89PNG"}
        )
        assert _rrn() in (extract_document(document).text or "")


def test_documents_with_leading_bytes_are_still_documents() -> None:
    """★ 앞에 1바이트를 붙인 DOCX 를 이진으로 보냈다. PDF 머리는 앞 1KB 안만 본다(PDF 리더의
    관례)."""
    prefixed = b"\x00" + _docx(f"주민번호 {_rrn()}")
    assert _rrn() in (extract_document(prefixed).text or "")
    assert attachments.document_kind(b"\x00" * 500 + _pdf("Tel 010-1234-5678")) == "pdf"
    # 간접 객체가 없는 `%PDF-` 는 텍스트다.
    assert attachments.document_kind(b"note: files start with %PDF-1.7") is None


def test_text_with_an_example_pdf_after_it_is_still_text() -> None:
    """★ `%PDF-` 를 어디서든 찾던 판은 전화번호 뒤에 예제 PDF 를 붙인 텍스트를 PDF 로 읽고 번호를
    버렸다."""
    note = ("메모 " * 600 + f"주민번호 {_rrn()}\n").encode() + _pdf("Hello world")
    assert attachments.document_kind(note) is None
    assert _proxy_status(_request(note, "text/plain"))[0] == 400


def test_a_pdf_with_a_document_appended_is_read_both_ways() -> None:
    """★ zip 을 먼저 봐서, PDF 뒤에 평범한 DOCX 를 붙이면 PDF 의 번호를 읽지 않았다."""
    pytest.importorskip("pypdf")
    text = extract_document(_pdf("Tel 010-1234-5678") + _docx("회의록")).text or ""
    assert "010-1234-5678" in text and "회의록" in text
    prefixed = extract_document(b"Tel 010-9876-5432\n" + _pdf("Hello")).text or ""
    assert "010-9876-5432" in prefixed  # PDF 머리 앞(1KB 안)의 글도 읽는다


def test_an_unreadable_real_pdf_is_not_inspected_as_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ `[docs]` 없이 받은 ASCII PDF 를 원문 텍스트로 "검사" 하고 허용했다 — 스트림은 인코딩돼
    있다."""
    monkeypatch.setitem(sys.modules, "pypdf", None)
    status, headers = _proxy_status(_request(_pdf("Tel 010-1234-5678"), "application/pdf"))
    assert status == 400 and "attachment_unreadable" in headers["X-Garim-Degraded-Reasons"]


def test_spreadsheet_values_with_entities_and_formula_literals_are_read() -> None:
    """★ `<v>` 의 엔티티(`&#57;`)를 풀지 않았고 수식 `<f>` 의 문자열 상수를 읽지 않았다."""
    encoded = "".join(f"&#{ord(c)};" for c in _rrn())
    sheet = (
        f'<worksheet><sheetData><row><c t="str"><v>{encoded}</v></c>'
        f'<c t="str"><f>"{_rrn()}"</f></c></row></sheetData></worksheet>'
    )
    workbook = _zip({"xl/workbook.xml": "<workbook/>", "xl/worksheets/sheet1.xml": sheet})
    assert (extract_document(workbook).text or "").count(_rrn()) == 2


def test_tabs_and_shared_string_references_count_against_the_budget() -> None:
    """★ 셀 탭 · 공유 문자열 참조를 세지 않아 888바이트 엑셀이 상한 1만 자에 500만 자를 냈다."""
    cells = '<c t="s"><v>0</v></c>' * 5_000
    workbook = _zip(
        {
            "xl/workbook.xml": "<workbook/>",
            "xl/sharedStrings.xml": "<sst><si><t>" + "가" * 1000 + "</t></si></sst>",
            "xl/worksheets/sheet1.xml": (
                f"<worksheet><sheetData><row>{cells}</row></sheetData></worksheet>"
            ),
        }
    )
    result = extract_document(workbook, max_chars=10_000)
    assert result is not None and result.reason == "too_large"
    # 이어진 구분자는 하나로 줄여 내놓는다 — 빈 셀 5만 개는 글이 없는 문서다.
    tabs = _zip({"xl/workbook.xml": "<workbook/>", "xl/worksheets/s.xml": "<c/>" * 50_000})
    assert extract_document(tabs, max_chars=10_000).reason == "no_text"


def test_whitespace_tidying_is_linear() -> None:
    """★ `[ \\t]*\\n` 정규식이 긴 공백 줄에서 제곱 시간이었다."""
    import time

    started = time.perf_counter()
    assert attachments._tidy(" " * 400_000 + "x\n" + "\t" * 400_000) == "x"
    assert time.perf_counter() - started < 1


def test_a_data_url_with_a_stray_character_is_still_an_attachment() -> None:
    """★ 엄격한 base64 해석에 실패하면 평문으로 봐서 첨부로 세지 않았다(관대한 해석기는 원본을
    푼다)."""
    url = (
        "data:application/octet-stream;base64,"
        + base64.b64encode(_docx("연락처 010-1234-5678")).decode()
    )
    for broken in (url[:60] + "!" + url[60:], " " + url):
        request = {
            "model": "x",
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": broken}}]}
            ],
        }
        assert _proxy_status(request)[0] == 400


def test_documents_per_request_are_capped() -> None:
    """★ PDF 하나마다 새 파이썬을 띄우는데 첨부 수에 상한이 없었다."""
    url = "data:application/octet-stream;base64," + base64.b64encode(_docx("회의록")).decode()
    parts = [{"type": "file", "file": {"file_data": url}}] * (
        attachments.MAX_DOCUMENTS_PER_REQUEST + 1
    )
    status, headers = _proxy_status(
        {"model": "x", "messages": [{"role": "user", "content": parts}]}
    )
    assert status == 400 and "attachment_unreadable" in headers["X-Garim-Degraded-Reasons"]
    within = parts[: attachments.MAX_DOCUMENTS_PER_REQUEST]
    assert (
        _proxy_status({"model": "x", "messages": [{"role": "user", "content": within}]})[0] == 200
    )


def test_a_text_attachment_in_a_text_part_stays_a_data_url() -> None:
    """★ 텍스트 파트의 사본에서 첨부를 풀어, 가린 평문이 data URL 자리에 그대로 쓰였다."""
    note = base64.b64encode("연락처 kim@example.com 입니다".encode()).decode()
    part = {"type": "text", "text": "요약", "image_url": f"data:text/plain;base64,{note}"}
    upstream = _Upstream()
    status, _, _ = _proxy(upstream).handle_chat_completions(
        {"model": "x", "messages": [{"role": "user", "content": [part]}]}, {}
    )
    sent = upstream.calls[0]["messages"][0]["content"][0]["image_url"]
    assert status == 200 and sent.startswith("data:text/plain;base64,")
    decoded = base64.b64decode(sent.split(",", 1)[1]).decode()
    assert "kim@example.com" not in decoded and "<EMAIL_1>" in decoded


def test_scan_applies_unmaskable_to_documents_split_into_windows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """★ 입력 상한보다 긴 문서는 창으로 나눠 마스킹본이 없어서, 전화번호가 정책을 비껴가 0
    이었다."""
    from garim.cli import main

    (tmp_path / "long.docx").write_bytes(
        _docx("회의록 내용입니다. " * 12_000, "연락처 010-2345-6789")
    )
    assert main(["scan", str(tmp_path / "long.docx")]) == 2
    assert "unmaskable document" in capsys.readouterr().err


# ── 최종 검토의 재검토에서 찾은 결함 ─────────────────────────────────────────


def test_xml_parts_that_start_with_whitespace_are_read() -> None:
    """★ 앞 64바이트만 봐서, 공백 100칸 뒤의 본문 XML 을 그림처럼 건너뛰었다."""
    body = " " * 100 + f"<w:document><w:t>주민번호 {_rrn()}</w:t></w:document>"
    document = _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": body})
    assert _rrn() in (extract_document(document).text or "")


def test_a_font_that_happens_to_start_with_a_bracket_is_not_xml() -> None:
    import random

    rng = random.Random(7)
    font = "<" + "".join(chr(rng.randrange(0x80, 0x100)) for _ in range(5000))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document><w:t>회의록</w:t></w:document>")
        archive.writestr("word/fonts/font1.odttf", font.encode("latin-1"))
    assert extract_document(buffer.getvalue()).text == "회의록"


def test_a_formula_does_not_glue_onto_the_cell_value() -> None:
    """★ 수식 글이 셀 값에 붙어 `C1900101-…` 가 되어 주민번호를 놓쳤다."""
    sheet = (
        f'<worksheet><row><c t="str"><f>B1&amp;"-"&amp;C1</f><v>{_rrn()}</v></c></row></worksheet>'
    )
    workbook = _zip({"xl/workbook.xml": "<workbook/>", "xl/worksheets/sheet1.xml": sheet})
    assert _proxy_status(_request(workbook, "application/octet-stream"))[0] == 400


def test_many_formatted_empty_cells_do_not_exhaust_the_budget() -> None:
    """★ 서식만 있는 빈 셀 10만 개의 탭이 상한을 넘겨 평범한 엑셀이 막혔다."""
    rows = "".join(f'<row r="{n}">' + '<c s="1"/>' * 26 + "</row>" for n in range(4000))
    sheet = f"<worksheet><sheetData>{rows}</sheetData></worksheet>"
    workbook = _zip(
        {
            "xl/workbook.xml": "<workbook/>",
            "xl/worksheets/sheet1.xml": sheet.replace("</row>", "</row>", 1).replace(
                '<c s="1"/>', '<c s="1"><v>7</v></c>', 1
            ),
        }
    )
    status, headers = _proxy_status(_request(workbook, "application/octet-stream"))
    assert status == 200, headers


def test_plain_text_in_a_base64_slot_is_inspected_as_text() -> None:
    """★ base64 글자가 대부분인 영어 평문이 관대하게 풀려 이진이 되고, 전화번호가 그대로 나갔다."""
    text = "Please call me back about the invoice " * 8 + "at 010-1234-5678 tomorrow"
    upstream = _Upstream()
    request = {
        "model": "x",
        "messages": [{"role": "user", "content": [{"type": "file", "file": {"file_data": text}}]}],
    }
    status, _, _ = _proxy(upstream).handle_chat_completions(request, {})
    assert status == 200 and "010-1234-5678" not in json.dumps(upstream.calls[0])


def test_a_broken_zip64_locator_does_not_crash() -> None:
    """★ 디스크 번호가 틀린 zip64 로케이터에 `is_zipfile` 이 BadZipFile 을 흘려 500 이 났다."""
    import struct

    blob = b"\x89PNG\r\n\x1a\n" + b"\0" * 300
    blob += struct.pack("<4sLQL", b"PK\x06\x07", 1, 0, 2)
    blob += struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0, 0, 0, 0, 0)
    assert extract_document(blob) is None
    assert _proxy_status(_request(blob, "image/png", part="image"))[0] == 200


def test_long_tag_names_are_linear() -> None:
    """★ 태그 정규식이 이름 길이에 제곱이었다(`<` + 글자 2만 개에 11초)."""
    import time

    bomb = _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": "<" + "a" * 200_000})
    started = time.perf_counter()
    extract_document(bomb)
    assert time.perf_counter() - started < 2


# ── 세 번째 재검토에서 찾은 결함 ─────────────────────────────────────────────


def _bare_file(data: str, slot: str = "file") -> dict[str, Any]:
    part = (
        {"type": "file", "file": {"file_data": data}}
        if slot == "file"
        else {"type": "input_audio", "input_audio": {"data": data, "format": "wav"}}
    )
    return {"model": "x", "messages": [{"role": "user", "content": [part]}]}


@pytest.mark.parametrize("slot", ["file", "audio"])
def test_base64_split_by_spaces_is_still_decoded(slot: str) -> None:
    """★ 공백이 든 값을 풀기 전에 평문으로 봐서, 76자마다 공백으로 나눈 DOCX 가 그대로
    나갔다."""
    b64 = base64.b64encode(_docx(f"주민번호 {_rrn()}")).decode()
    spaced = " ".join(b64[i : i + 76] for i in range(0, len(b64), 76))
    assert _proxy_status(_bare_file(spaced, slot))[0] == 400


def test_an_empty_embedded_document_does_not_hide_the_outer_text() -> None:
    """★ 글 없는 내장 문서 하나로 바깥 문서 전체를 `no_text` 로 보고 본문의 주민번호를 버렸다."""
    outer = _zip(
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": f"<w:document><w:t>주민번호 {_rrn()}</w:t></w:document>",
        }
    )
    with zipfile.ZipFile(io.BytesIO(outer), "a") as archive:
        archive.writestr("word/embeddings/empty.docx", _docx(""))
    assert _rrn() in (extract_document(outer).text or "")


@pytest.mark.parametrize(
    "encode",
    [
        lambda xml: (" " * 4095 + xml).encode(),
        lambda xml: ("﻿" + " " * 3000 + xml).encode("utf-16-le"),
        lambda xml: xml.encode("utf-16-le"),
    ],
    ids=["4095-spaces", "utf16-bom-spaces", "utf16-no-bom"],
)
def test_main_parts_are_recognised_whatever_their_leading_bytes(encode: Any) -> None:
    """★ 앞 4KB 의 공백 · BOM 뒤 공백 · BOM 없는 UTF-16 본문을 XML 이 아니라고 건너뛰었다."""
    xml = f"<w:document><w:p><w:r><w:t>주민번호 {_rrn()}</w:t></w:r></w:p></w:document>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", encode(xml))
        archive.writestr("docProps/core.xml", "<cp:coreProperties><dc:title>회의록</dc:title>")
    assert _rrn() in (extract_document(buffer.getvalue()).text or "")


def test_an_unrecognisable_main_part_is_unreadable() -> None:
    """본문 파트가 XML 로 읽히지 않으면(`.xlsb` 의 이진 시트 등) 다 읽었다고 하지 않는다."""
    workbook = _zip(
        {"[Content_Types].xml": "<Types/>", "xl/worksheets/sheet1.xml": "\x00\x01\x02binary"}
    )
    assert extract_document(workbook).reason == "unreadable"


def test_doctype_instructions_and_split_entities_do_not_hide_text() -> None:
    """★ 부분집합의 처리 지시 속 `'` 를 따옴표로 읽어 본문을 삼켰고, `&a;&b;` 로 나눈 번호를
    놓쳤다."""
    head, tail = _rrn()[:7], _rrn()[7:]
    for xml in (
        f"<!DOCTYPE d [<?pi it's?>]><w:document><w:t>Kim's {head}</w:t><w:t>{tail}</w:t>"
        "</w:document>",
        f'<!DOCTYPE d [<!ENTITY a "{head}"><!ENTITY b "{tail}">]><w:document><w:t>&a;&b;</w:t>'
        "</w:document>",
    ):
        document = _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": xml})
        assert _proxy_status(_request(document, "application/octet-stream"))[0] == 400


def test_entity_expansion_is_bounded() -> None:
    refs = "&a;" * 50_000
    xml = f'<!DOCTYPE d [<!ENTITY a "{"가" * 1000}">]><w:document><w:t>{refs}</w:t></w:document>'
    document = _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": xml})
    assert extract_document(document, max_chars=100_000).reason == "too_large"


def test_text_before_a_zip_is_read() -> None:
    """★ 주민번호 한 줄 뒤에 DOCX 를 붙인 텍스트 첨부에서 DOCX 만 읽었다."""
    payload = f"고객 주민번호 {_rrn()}\n".encode() + _docx("회의록")
    assert _proxy_status(_request(payload, "text/plain"))[0] == 400


def test_lenient_data_urls_still_count_as_attachments(tmp_path: Path) -> None:
    """★ 문자 하나를 끼운 그림 data URL 을 평문으로 봐서 `uninspected: block` 을 비껴갔다. 조각을
    이어 붙인 base64 는 해석기처럼 첫 패딩에서 멈춰 푼다."""
    policy = _policy(tmp_path, uninspected="block")
    url = "data:image/png;base64," + base64.b64encode(_png()).decode()
    stray = url[:40] + "." + url[40:]
    request = {
        "model": "x",
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": stray}}]}
        ],
    }
    assert _proxy_status(request, policy)[0] == 400
    chunks = base64.b64encode(_docx(f"주민번호 {_rrn()}") + b"!").decode()
    assert chunks.endswith("=")
    joined = "data:application/octet-stream;base64," + chunks + base64.b64encode(b"tail").decode()
    assert _proxy_status(_bare_file(joined))[0] == 400


def test_a_space_before_base64_in_a_data_url_is_accepted() -> None:
    url = (
        "data:application/octet-stream; base64,"
        + base64.b64encode(_docx(f"주민번호 {_rrn()}")).decode()
    )
    assert _proxy_status(_bare_file(url))[0] == 400


def test_a_cp949_note_before_a_pdf_is_read() -> None:
    pytest.importorskip("pypdf")
    text = extract_document("고객 연락처 010-2345-6789\n".encode("cp949") + _pdf("Hello")).text
    assert "010-2345-6789" in (text or "")


def test_embedded_documents_share_one_decompression_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 내장 문서마다 64MB 를 새로 줘서 내장 문서 4개짜리 1.9KB DOCX 가 40초를 썼다."""
    monkeypatch.setattr(attachments, "MAX_UNCOMPRESSED_BYTES", 60_000)
    inner = _docx("가" * 8_000)  # 압축을 풀면 약 24KB
    outer = _docx("바깥")
    with zipfile.ZipFile(io.BytesIO(outer), "a") as archive:
        for n in range(4):
            archive.writestr(f"word/embeddings/d{n}.docx", inner)
    buffer = io.BytesIO(outer)
    with zipfile.ZipFile(buffer, "a") as archive:
        for n in range(4):
            archive.writestr(f"word/embeddings/d{n}.docx", inner)
    assert extract_document(buffer.getvalue()).reason == "too_large"


def test_office_extraction_has_a_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attachments, "OFFICE_TIMEOUT_SECONDS", -1.0)
    assert extract_document(_docx("회의록")).reason == "timeout"


def test_alt_chunk_html_and_text_parts_are_read() -> None:
    """워드 altChunk(`afchunk.htm` · `.txt`)는 워드가 그대로 보여 준다."""
    document = _zip(
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": "<w:document><w:t>본문</w:t></w:document>",
            "word/afchunk.htm": f"<html><body><p>주민번호 {_rrn()}</p></body></html>",
            "word/afchunk2.txt": "연락처 010-2345-6789",
        }
    )
    text = extract_document(document).text or ""
    assert _rrn() in text and "010-2345-6789" in text


def test_svg_images_are_read_but_never_rewritten() -> None:
    """★ SVG data URL 을 텍스트로 보고 가려 다시 인코딩하면서 경로 좌표를 IP 주소로 가려 그림을
    깨뜨렸다."""
    clean = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M1.5.5.5L10.20.30.40"/></svg>'
    url = "data:image/svg+xml;base64," + base64.b64encode(clean.encode()).decode()
    upstream = _Upstream()
    request = {
        "model": "x",
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ],
    }
    status, _, _ = _proxy(upstream).handle_chat_completions(request, {})
    assert (
        status == 200 and upstream.calls[0]["messages"][0]["content"][0]["image_url"]["url"] == url
    )
    labelled = clean.replace("</svg>", "<text>연락처 kim@example.com</text></svg>")
    assert _proxy_status(_request(labelled.encode(), "image/svg+xml", part="image"))[0] == 400


def test_libreoffice_metadata_and_decimal_cells_are_not_pii() -> None:
    """★ LibreOffice 생성기 줄(`LibreOffice/24.8.4.2`)을 IP 로, 소수 셀을 카드로 잡아 평범한 문서를
    막았다."""
    from garim.detect.rules.common_pii import detect_common_pii

    odt = _zip(
        {
            "mimetype": "application/vnd.oasis.opendocument.text",
            "content.xml": (
                "<office:document-content><text:p>회의록</text:p></office:document-content>"
            ),
            "meta.xml": "<office:meta><meta:generator>LibreOffice/24.8.4.2$MacOSX_AARCH64"
            "</meta:generator></office:meta>",
        }
    )
    assert _proxy_status(_request(odt, "application/octet-stream"))[0] == 200
    assert detect_common_pii("셀 값 0.4532015112830366") == []
    assert detect_common_pii("서버 10.20.30.40")  # 진짜 주소는 그대로 잡는다


def test_lenient_bare_base64_is_trusted_when_it_looks_like_base64(tmp_path: Path) -> None:
    """맨 base64 자리(`input_audio.data`)의 문자 하나 낀 오디오 · 조각을 이어 붙인 텍스트도
    해석기처럼 푼다 — 공백이 없고 거의 다 base64 글자일 때만."""
    audio = base64.b64encode(b"RIFF" + bytes(range(256)) * 4).decode()
    stray = audio[:50] + "." + audio[50:]
    assert (
        _proxy_status(_bare_file(stray, "audio"), _policy(tmp_path, uninspected="block"))[0] == 400
    )
    note = base64.b64encode(f"주민번호 {_rrn()}\n".encode()).decode()
    assert note.endswith("=")
    chunked = note + base64.b64encode("회의록 내용 ".encode() * 30).decode()
    assert _proxy_status(_bare_file(chunked))[0] == 400


def test_a_data_url_whose_body_is_not_base64_is_inspected_as_text() -> None:
    text = "Please call me back about the invoice " * 8 + "at 010-1234-5678 tomorrow"
    upstream = _Upstream()
    request = {
        "model": "x",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": f"data:;base64,{text}"}}],
            }
        ],
    }
    status, _, _ = _proxy(upstream).handle_chat_completions(request, {})
    assert status == 200 and "010-1234-5678" not in json.dumps(upstream.calls[0])
