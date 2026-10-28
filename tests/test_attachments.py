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
    (tmp_path / "broken.xlsx").write_bytes(b"PK\x03\x04 not really a zip")
    (tmp_path / "ok.txt").write_text("안녕하세요\n", encoding="utf-8")
    assert main(["scan", str(tmp_path)]) == 3
    assert "broken.xlsx" in capsys.readouterr().err
