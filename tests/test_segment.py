from garim.segment import detect_content_type, split_text


def test_split_is_lossless_and_marks_code_fence() -> None:
    source = "설명입니다.\n```python\nvalue = 1\n```\n마무리입니다.\n"
    parts = split_text(source)
    assert "".join(part.text for part in parts) == source
    assert [part.content_type for part in parts] == ["prose", "code", "prose"]
    assert all(left.end == right.start for left, right in zip(parts, parts[1:], strict=False))


def test_content_types_are_deterministic() -> None:
    assert detect_content_type('{"email": "masked"}') == "structured"
    assert detect_content_type("2026-01-01 ERROR request failed\n2026-01-01 INFO retry") == "log"
    assert detect_content_type("def run():\n    return 1\n") == "code"
    assert detect_content_type("일반적인 한국어 문장입니다.") == "prose"


def test_long_input_tail_is_still_inspected() -> None:
    """★ 회귀 — 청킹이 없어 L1 이 512토큰에서 조용히 자르던 fail-open.

    47,999자 입력에 L1 infer 가 1회, 전체 텍스트 그대로 호출되고 앞부분만
    검사된 채 degraded=False 로 보고됐다. 뒷부분 PII 는 그냥 통과했다.
    """
    from garim.api import Inspector

    inspector = Inspector()
    tail = "고객 주민등록번호 900101-1234561 입니다"
    text = "가" * (47_999 - len(tail)) + tail
    result = inspector.check(text)
    assert any(finding.code == "L-KR-01" for finding in result.leaks)
    assert result.verdict == "block"


def test_oversize_input_degrades_instead_of_raising() -> None:
    """§3.6 — 검사기가 앱을 멈추면 사용자가 꺼버리고, 그러면 아무것도 못 막는다."""
    from garim.api import Inspector

    inspector = Inspector()
    result = inspector.check("가" * 100_001)
    assert result.degraded is True
    assert result.verdict == "warn"


def test_chunks_overlap_and_keep_source_offsets() -> None:
    from garim.segment.chunk import chunk_text

    text = "".join(str(index % 10) for index in range(1000))
    chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)
    assert len(chunks) > 1
    for chunk in chunks:
        assert text[chunk.start : chunk.start + len(chunk.text)] == chunk.text
    assert chunks[1].start < chunks[0].start + len(chunks[0].text), "오버랩이 없다"
