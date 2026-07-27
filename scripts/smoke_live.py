"""라이브 스모크 — 실제 Gemini 로 1 에피소드 + 카세트 record→replay 왕복.

⚠️ 무료 티어 5 RPM. 이 스크립트는 소수 호출만 한다. 반복 실행 금지.

    python -m scripts.smoke_live

검증:
 1. GeminiProvider 의 Msg↔Content 변환 + 실제 functionCall 파싱
 2. 4 seam 루프가 실제 모델로 완주
 3. 카세트 record 후 FakeProvider replay 가 동일 결과를 내는가
"""

from __future__ import annotations

import asyncio
import json

from dotenv import load_dotenv

from src.config import ROOT, gen_config, load_policy
from src.providers.cassette import CassetteStore
from src.providers.fake import FakeProvider
from src.providers.gemini import GeminiProvider
from src.providers.recording import RecordingProvider
from src.agent.loop import run_episode


async def main() -> None:
    load_dotenv(ROOT / ".env")
    policy = load_policy()
    cfg = gen_config(policy)
    print(f"모델: {cfg.model} · 도구 {len(cfg.tools)}종")

    store = CassetteStore(ROOT / "data" / "cassettes", mode="record", on_miss="record")
    recorder = RecordingProvider(GeminiProvider(), store, cfg)

    task = "전화번호 010-1234-5678 고객의 최근 환불 상태를 조회해서 한 문장으로 알려줘."
    print(f"\n[LIVE] {task}")
    ep = await run_episode(task, recorder, cfg, scenario="smoke")

    print(f"  finish={ep.finish_reason} · hops={ep.total_hops} · {ep.total_latency_ms}ms")
    for h in ep.hops:
        if h.tool_name:
            print(f"   · {h.tool_name}({h.tool_args}) executed={h.executed} gate={h.gate_decision}")
    print(f"  최종: {ep.final_text[:120]}")

    # ── 카세트 재생 검증 ──
    print(f"\n[REPLAY] 카세트 {len(store._data)}건으로 재생")
    replay_store = CassetteStore(ROOT / "data" / "cassettes", mode="replay", on_miss="error")
    fake = FakeProvider(cassette=replay_store)
    ep2 = await run_episode(task, fake, cfg, scenario="smoke-replay")
    print(f"  finish={ep2.finish_reason} · hops={ep2.total_hops}")

    same = (ep.finish_reason == ep2.finish_reason
            and [h.tool_name for h in ep.hops] == [h.tool_name for h in ep2.hops])
    print(f"\n★ record→replay 동일: {'통과' if same else '불일치'}")


if __name__ == "__main__":
    asyncio.run(main())
