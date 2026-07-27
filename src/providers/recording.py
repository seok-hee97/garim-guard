"""RecordingProvider — 실제 provider 를 감싸 응답을 카세트에 녹화한다.

키는 FakeProvider 가 재생 시 계산하는 것과 **정확히 동일**해야 한다
(cassette_key(model, fingerprint, contents)). 그래야 record→replay 가 맞물린다.

5 RPM 무료 티어에서 애블레이션을 돌리는 표준 절차:
  1. mode=record 로 RecordingProvider 를 통해 1회 스윕 (실제 호출, 느림)
  2. 이후 모든 arm 은 FakeProvider(cassette, mode=replay) 로 (호출 0, 빠름)

⚠️ 알려진 한계 (D6 에서 처리) — Gemini 3.x thought_signature:
   신선한 녹화 실행은 문제없다(원본 Content 가 메모리에 있어 서명 보존).
   그러나 **에피소드 중간부터 재개 녹화**하면, 캐시에서 온 앞선 모델 턴에는
   서명이 없어(카세트에 서명 미저장) 뒤이은 라이브 호출이 400 을 낼 수 있다.
   → 재개는 **에피소드 단위**로만 한다(한 에피소드는 통째로 신선 녹화하거나 통째로 캐시).
   eval/run.py 는 episode_id 단위로 건너뛰므로 이 규율을 자연히 만족한다.
"""

from __future__ import annotations

from .base import GenConfig, GenResult, Msg, Provider
from .cassette import CassetteStore, cassette_key
from .fake import _fingerprint, _msgs_to_contents


def _result_to_dict(r: GenResult) -> dict:
    return {
        "text": r.text,
        "tool_calls": [{"name": c.name, "args": c.args, "call_id": c.call_id}
                       for c in r.tool_calls],
        "blocked": r.blocked,
        "block_info": r.block_info,
        "finish_reason": r.finish_reason,
        "usage": r.usage,
    }


class RecordingProvider:
    name = "recording"

    def __init__(self, inner: Provider, store: CassetteStore, cfg: GenConfig):
        self._inner = inner
        self._store = store
        self._fp = _fingerprint(cfg)

    async def generate(self, messages: list[Msg], cfg: GenConfig) -> GenResult:
        key = cassette_key(cfg.model, self._fp, _msgs_to_contents(messages))
        cached = self._store.get(key)
        if cached is not None:
            from .fake import _result_from_dict
            return _result_from_dict(cached)
        result = await self._inner.generate(messages, cfg)
        self._store.put(key, _result_to_dict(result))  # synthetic=False (실제 호출)
        return result
