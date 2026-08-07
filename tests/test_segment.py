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


def test_l1_calls_are_bounded_but_l0_still_scans_everything() -> None:
    """★ 회귀 — 청크 수에 상한이 없어 L1 이 무한정 불리던 것(§3.6).

    `max_segments` 가드는 `split_text` 결과만 세고 `chunk_text` 결과를 세지 않았다.
    10만자 입력에 L1 `infer` 가 **447회** 불리고 `degraded=False` 로 보고됐다.
    §7.1.3 의 세그먼트당 L1 예산 160ms 를 곱하면 단일 호출이 약 71초다.

    ★ 그리고 상한을 스캔 전체에 걸면 안 된다 — 위 꼬리 테스트가 잡는다.
    L0 는 전량 돌고 L1 만 묶인다. 두 단언이 함께 있어야 어느 쪽으로도 못 무너진다.
    """
    from garim.api import Inspector
    from garim.detect.l1 import L1Output

    calls: list[int] = []

    class Counting:
        name = "counting"

        def infer(self, text, *, normalization_delta, origin, content_type):  # noqa: ANN001
            calls.append(len(text))
            return L1Output()

    inspector = Inspector(l1_backend=Counting())
    cap = inspector.policy.config.limits.max_l1_chunks
    tail = "고객 주민등록번호 900101-1234561 입니다"
    text = "가" * (60_000 - len(tail)) + tail
    result = inspector.check(text)

    assert len(calls) <= cap, f"L1 이 상한 {cap} 을 넘겨 {len(calls)}회 불렸다"
    assert result.degraded is True
    assert "l1_chunk_cap" in result.degraded_reasons
    # L0 는 멈추지 않는다 — 꼬리의 주민등록번호를 여전히 잡아야 한다.
    assert any(finding.code == "L-KR-01" for finding in result.leaks)


def test_oversize_input_blocks_uninspected_tail_instead_of_raising() -> None:
    """검사하지 않은 꼬리를 warn 상태로 외부 전송할 수 없어야 한다."""
    from garim.api import Inspector

    inspector = Inspector()
    result = inspector.check("가" * 100_001)
    assert result.degraded is True
    assert result.verdict == "block"
    assert "input_truncated" in result.degraded_reasons


def test_chunks_overlap_and_keep_source_offsets() -> None:
    from garim.segment.chunk import chunk_text

    text = "".join(str(index % 10) for index in range(1000))
    chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)
    assert len(chunks) > 1
    for chunk in chunks:
        assert text[chunk.start : chunk.start + len(chunk.text)] == chunk.text
    assert chunks[1].start < chunks[0].start + len(chunks[0].text), "오버랩이 없다"
