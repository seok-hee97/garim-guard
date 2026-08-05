"""세그먼트를 L1 입력 길이에 맞춰 청크로 나눈다 — DESIGN §3.6 · §3.7 · §10.1.

★ 이 모듈이 없어서 fail-open 이 있었다.

초판은 `garim/detect/l1.py` 가 `truncation=True, max_length=512` 만 걸고 청킹을 하지
않았다. `Inspector` 는 `max_input_chars=100_000` 을 허용하므로, 47,999자 입력에
L1 `infer` 가 **1회, 전체 텍스트 그대로** 호출되고 512토큰이 커버하는 앞부분만
검사된 채 `degraded=False` 로 보고됐다. 뒷부분에 있는 공격·PII 는 조용히 통과했다.

§3.7 의 `chunk_size` / `chunk_overlap` 도 선언만 되고 아무도 읽지 않았다.

── 좌표계 ──────────────────────────────────────────────────
청크의 `start` 는 **원본 세그먼트 텍스트 기준** 문자 오프셋이다. 파이프라인이
`source_offset` 으로 더해 `Finding.span` 을 원문 좌표로 만든다(§3.2.2).

── 오버랩 중복 ─────────────────────────────────────────────
오버랩 구간에서 같은 것이 두 번 잡힐 수 있다. 원문 좌표로 겹치므로
`merge.merge_overlaps` 의 기존 규칙(심각도 → 길이 → L0 → 스트림)이 그대로 흡수한다.
청킹 전용 병합 규칙을 새로 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 한국어는 문자당 약 1~2토큰이다. 토크나이저 없이 청킹하려면 보수적으로 잡아야
# 하고, 넘치면 L1 이 잘라서 fail-open 이 된다. 토크나이저가 있으면
# `chunk_by_tokens` 를 쓴다 — 그쪽이 정확하다.
_CHARS_PER_TOKEN = 0.5


@dataclass(frozen=True)
class Chunk:
    text: str
    start: int


def _char_budget(token_budget: int) -> int:
    return max(1, int(token_budget * _CHARS_PER_TOKEN))


def chunk_text(
    text: str,
    *,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    tokenizer=None,
) -> list[Chunk]:
    """텍스트를 청크로 나눈다. 토크나이저가 있으면 토큰 기준, 없으면 보수적 문자 기준.

    `chunk_size` / `chunk_overlap` 은 **토큰** 단위다(§3.7).
    """
    if not text:
        return [Chunk("", 0)]
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    if tokenizer is not None:
        return _chunk_by_tokens(text, tokenizer, chunk_size, chunk_overlap)

    size = _char_budget(chunk_size)
    stride = size - _char_budget(chunk_overlap)
    if len(text) <= size:
        return [Chunk(text, 0)]
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        chunks.append(Chunk(text[start : start + size], start))
        if start + size >= len(text):
            break
        start += stride
    return chunks


def _chunk_by_tokens(text: str, tokenizer, chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    """토크나이저의 offset_mapping 으로 정확한 토큰 경계를 쓴다.

    특수토큰은 `offset == (0, 0)` 으로 거른다 — 개수로 세면 백본마다 다르다(§4.2.3 함정 3).
    """
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    offsets = [span for span in encoded["offset_mapping"] if span != (0, 0) or span[1] > 0]
    if not offsets:
        return [Chunk(text, 0)]
    # 특수토큰 2개 자리를 남긴다.
    budget = max(1, chunk_size - 2)
    if len(offsets) <= budget:
        return [Chunk(text, 0)]

    stride = max(1, budget - chunk_overlap)
    chunks: list[Chunk] = []
    index = 0
    while index < len(offsets):
        window = offsets[index : index + budget]
        start = window[0][0]
        end = window[-1][1]
        chunks.append(Chunk(text[start:end], start))
        if index + budget >= len(offsets):
            break
        index += stride
    return chunks
