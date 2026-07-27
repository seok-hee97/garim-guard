"""D0 환경 점검 — 키 · 모델 ID · 도구호출 · 쿼터 실측.

오늘의 하드 게이트는 "Gemini 도구호출 1회 성공"이다. 이 스크립트가 그것을 판정한다.

    python -m scripts.check_env            # 기본 점검
    python -m scripts.check_env --rpm 8    # 쿼터 실측까지 (8회 연속 호출)

결과는 results/env_report.json 에 남는다. 기획서 7장(개발 환경)이 이 파일에서 생성된다.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "results" / "env_report.json"

# 도구호출 왕복을 검증하기 위한 최소 도구 1종.
# 실제 mock 도구(tools.py)와 무관하며, 오직 functionCall part 가 오는지만 본다.
PROBE_TOOL = {
    "name": "lookup_customer",
    "description": "고객 정보를 조회한다.",
    "parameters": {
        "type": "object",
        "properties": {
            "phone": {"type": "string", "description": "고객 전화번호"},
        },
        "required": ["phone"],
    },
}
PROBE_PROMPT = "고객 전화번호 010-0000-0000 으로 고객 정보를 조회해 주세요."


def _fail(step: str, msg: str, hint: str = "") -> dict:
    return {"step": step, "ok": False, "error": msg, "hint": hint}


# google-genai SDK 는 두 이름을 모두 인식한다. 사용자가 어느 쪽에 넣어도 동작하게 한다.
KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def _resolve_key() -> tuple[str, str]:
    """.env 를 로드하고 두 후보 중 채워진 키를 돌려준다. (name, value)"""
    for name in KEY_NAMES:
        v = os.getenv(name, "").strip()
        if v:
            # google-genai 는 GOOGLE_API_KEY 를 우선 참조하므로 정규화해 둔다.
            os.environ["GOOGLE_API_KEY"] = v
            return name, v
    return "", ""


def check_key() -> dict:
    """.env 를 읽고 GEMINI_API_KEY 또는 GOOGLE_API_KEY 존재를 확인한다."""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        return _fail("key", "python-dotenv 미설치", "pip install -r requirements.core.txt")

    name, key = _resolve_key()
    if not key:
        return _fail(
            "key",
            f"{' / '.join(KEY_NAMES)} 가 모두 비어 있다",
            "https://aistudio.google.com/apikey 에서 발급 후 .env 에 "
            "GEMINI_API_KEY=... 또는 GOOGLE_API_KEY=... 로 기입.",
        )
    return {"step": "key", "ok": True, "source_var": name, "key_len": len(key),
            "key_prefix": key[:6] + "..."}


def check_tool_call(model: str) -> dict:
    """★ 오늘의 하드 게이트 ★ — 도구 선언을 넘기고 functionCall part 를 받는다.

    AFC(automatic_function_calling)를 반드시 끈다. 켜져 있으면 SDK 가 도구를 대신
    실행해버려 before_tool seam 이 발동할 기회 자체가 사라진다.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        return _fail("tool_call", f"google-genai 임포트 실패: {e}")

    try:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        t0 = time.perf_counter()
        resp = client.models.generate_content(
            model=model,
            contents=PROBE_PROMPT,
            config=types.GenerateContentConfig(
                tools=[types.Tool(function_declarations=[PROBE_TOOL])],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                safety_settings=[
                    types.SafetySetting(category=c, threshold="BLOCK_MEDIUM_AND_ABOVE")
                    for c in (
                        "HARM_CATEGORY_HARASSMENT",
                        "HARM_CATEGORY_HATE_SPEECH",
                        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "HARM_CATEGORY_DANGEROUS_CONTENT",
                    )
                ],
                temperature=0.2,
                max_output_tokens=256,
            ),
        )
        elapsed = time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 리포트에 남겨야 한다
        return _fail("tool_call", f"{type(e).__name__}: {e}")

    # 모든 part 를 순회한다. parts[0] 만 보면 병렬 호출을 놓친다 — 이 규율을 여기서부터 지킨다.
    calls = []
    cand = (resp.candidates or [None])[0]
    if cand and cand.content and cand.content.parts:
        for part in cand.content.parts:
            fc = getattr(part, "function_call", None)
            if fc:
                calls.append({"name": fc.name, "args": dict(fc.args or {})})

    usage = getattr(resp, "usage_metadata", None)
    return {
        "step": "tool_call",
        "ok": bool(calls),
        "model": model,
        "tool_calls": calls,
        "n_parts": len(cand.content.parts) if cand and cand.content and cand.content.parts else 0,
        "finish_reason": str(getattr(cand, "finish_reason", "")) if cand else "",
        "latency_s": round(elapsed, 3),
        "usage": {
            "prompt": getattr(usage, "prompt_token_count", None),
            "total": getattr(usage, "total_token_count", None),
        }
        if usage
        else None,
        "error": None if calls else "functionCall part 가 반환되지 않았다 (모델이 텍스트로만 응답)",
    }


def measure_rpm(model: str, n: int) -> dict:
    """연속 호출로 실제 처리율과 429 발생 지점을 잰다.

    무료 티어의 실제 RPM 을 모르면 eval 전량 스윕 시간을 산정할 수 없다.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    lat, errs = [], []
    t_start = time.perf_counter()
    for i in range(n):
        try:
            t0 = time.perf_counter()
            client.models.generate_content(
                model=model,
                contents=f"1+{i} 를 계산해 숫자만 답하라.",
                config=types.GenerateContentConfig(max_output_tokens=16, temperature=0),
            )
            lat.append(time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001
            errs.append({"i": i, "error": f"{type(e).__name__}: {e}"})
    total = time.perf_counter() - t_start
    ok = len(lat)
    lat_sorted = sorted(lat)
    return {
        "step": "rpm",
        "ok": ok > 0,
        "attempted": n,
        "succeeded": ok,
        "errors": errs,
        "wall_s": round(total, 2),
        "observed_rpm": round(ok / total * 60, 1) if total > 0 else None,
        "latency_p50_s": round(lat_sorted[len(lat_sorted) // 2], 3) if lat else None,
        "latency_max_s": round(max(lat), 3) if lat else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    # gemini-2.5-flash 는 2026-10-16 종료 예정 → GA 안정 버전 사용
    ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--rpm", type=int, default=0, help="연속 호출 횟수 (0=생략)")
    args = ap.parse_args()

    report: dict = {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.machine()}",
        "steps": [],
    }

    k = check_key()
    report["steps"].append(k)
    if not k["ok"]:
        _emit(report, gate=False)
        return 1

    tc = check_tool_call(args.model)
    report["steps"].append(tc)

    if tc["ok"] and args.rpm:
        report["steps"].append(measure_rpm(args.model, args.rpm))

    _emit(report, gate=tc["ok"])
    return 0 if tc["ok"] else 1


def _emit(report: dict, gate: bool) -> None:
    report["hard_gate_tool_call"] = gate
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for s in report["steps"]:
        mark = "✅" if s["ok"] else "❌"
        print(f"{mark} {s['step']}")
        if not s["ok"]:
            print(f"   → {s.get('error')}")
            if s.get("hint"):
                print(f"   → {s['hint']}")
        elif s["step"] == "tool_call":
            print(f"   → {s['tool_calls']}  ({s['latency_s']}s, parts={s['n_parts']})")
        elif s["step"] == "rpm":
            print(f"   → {s['succeeded']}/{s['attempted']} 성공, 관측 RPM≈{s['observed_rpm']}")

    print()
    print("★ D0 하드 게이트 (Gemini 도구호출 1회):", "통과" if gate else "미통과")
    print(f"   리포트: {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
