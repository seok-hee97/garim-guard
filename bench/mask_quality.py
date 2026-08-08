"""§8.1-13b — 마스킹이 응답 품질을 얼마나 깎는가. §7.1.1 목표 **저하 ≤ 0.10**.

§7.1.1 이 이 지표를 뒤늦게 넣으면서 적은 이유가 그대로 설계 근거다 — 문서가 세 곳에서
*"과탐은 곧 우회"* 라고 단언하면서 **그 1차 동인을 안 쟀다.** 사용자가 도구를 끄는 이유는
오탐 자체가 아니라 마스킹 때문에 답이 나빠지는 것이다.

## 어떻게 재는가

같은 과제를 **두 벌**로 같은 모델에 태운다 — 원문 한 벌, 마스킹본 한 벌. 정답은 구성상
알려져 있으므로 사람 판정이 필요 없다.

    저하 = 1 − (마스킹 정답률 / 원문 정답률)

## 과제를 세 갈래로 나눈 이유 — 첫 판이 천장에 붙었다

처음엔 분류 과제만 넣었더니 **양쪽 다 12/12** 라 저하가 0.000 으로 나왔다. 이건 마스킹이
무해하다는 증거가 아니라 **과제가 쉽다는 증거**다 — §7.6-(3) 이 금지한 동어반복이다.
그래서 마스킹이 답에 닿는 경로별로 갈랐다.

| 갈래 | 마스킹된 값이 답에 필요한가 | 기대 |
|---|---|---|
| `independent` | 전혀 아니다 (분류·감정) | 저하 0 이어야 한다 |
| `identity` | 값끼리 **같은지 다른지**만 | 저하 0 이어야 한다 — 번호가 보존해야 할 성질 |
| `content` | 값의 **내용 자체** | **못 푸는 게 정상이다** |

`identity` 는 모델이 아니라 **우리 마스커를 시험한다.** 같은 값은 같은 번호로, 다른 값은
다른 번호로 가려진다(`<PHONE_1>` 두 번 vs `<PHONE_1>`/`<PHONE_2>`). 이 성질이 응답까지
살아남는지는 문자열만 봐서는 알 수 없다.

★ **대표 수치는 `content` 를 뺀 것이다.** 연락처 뒷자리를 되묻는 과제에서 마스킹본이
못 맞히는 것은 결함이 아니라 도구가 제 일을 한 것이다. 그 갈래를 대표 수치에 넣으면
"가림이 잘 가릴수록 점수가 나빠지는" 지표가 된다. 대신 **빼고 숨기지 않는다** —
`intended_loss` 로 따로 낸다.

## §7.4 가 건 조건 둘 — 코드로 강제한다

- **실제 개인정보를 절대 보내지 않는다.** `assert_synthetic_only` 가 합성 값이 아닌 것을
  거부한다.
- **Gemini free tier 는 데이터가 학습에 쓰인다.** 코드가 tier 를 알 수 없으므로 실행 시
  경고하고 기록에 남긴다.

⚠️ 이 하네스는 **제품 경로가 아니다.** 검사기는 아무것도 외부로 보내지 않는다
(`tools/no_external_calls.py` 가 증명). API 키는 벤치에서만 쓴다.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Client = Callable[[str], str]

INDEPENDENT, IDENTITY, CONTENT = "independent", "identity", "content"
# 대표 수치에서 빼는 갈래. 여기서 나는 손실은 도구가 제 일을 한 결과다.
INTENDED_LOSS_FAMILIES = frozenset({CONTENT})


@dataclass(frozen=True, slots=True)
class Task:
    family: str
    body: str
    question: str
    gold: str


TASKS: tuple[Task, ...] = (
    # ── independent — 마스킹된 값이 답에 전혀 필요 없다 ──────────────────
    Task(
        INDEPENDENT,
        "김민수 고객님({p})께서 결제 취소와 환불을 요청하셨습니다.",
        "위 문의의 요청 유형을 환불, 배송, 교환, 문의 중 한 단어로만 답하세요.",
        "환불",
    ),
    Task(
        INDEPENDENT,
        "박지영 고객님({p})께서 주문하신 상품의 배송 현황을 물어보셨습니다.",
        "위 문의의 요청 유형을 환불, 배송, 교환, 문의 중 한 단어로만 답하세요.",
        "배송",
    ),
    Task(
        INDEPENDENT,
        "정하윤 고객님({e})께서 응대가 친절했다고 칭찬해 주셨습니다.",
        "위 문장의 감정을 긍정 또는 부정 중 한 단어로만 답하세요.",
        "긍정",
    ),
    Task(
        INDEPENDENT,
        "강도현 고객님({p})께서 처리가 너무 늦다고 불만을 제기하셨습니다.",
        "위 문장의 감정을 긍정 또는 부정 중 한 단어로만 답하세요.",
        "부정",
    ),
    # ── identity — 값끼리 같은지 다른지만 필요하다. 번호가 보존해야 할 성질 ──
    Task(
        IDENTITY,
        "1차 문의는 {p}, 2차 문의는 {p2} 로 접수됐습니다.",
        "두 연락처를 비교해 정확히 '동일' 또는 '상이' 한 단어로만 답하세요.",
        "상이",
    ),
    Task(
        IDENTITY,
        "1차 문의는 {p}, 2차 문의도 {p} 로 접수됐습니다.",
        "두 연락처를 비교해 정확히 '동일' 또는 '상이' 한 단어로만 답하세요.",
        "동일",
    ),
    Task(
        IDENTITY,
        "발신자는 {e}, 수신자는 {e2} 입니다.",
        "두 이메일 주소를 비교해 정확히 '동일' 또는 '상이' 한 단어로만 답하세요.",
        "상이",
    ),
    Task(
        IDENTITY,
        "발신자는 {e}, 수신자도 {e} 입니다.",
        "두 이메일 주소를 비교해 정확히 '동일' 또는 '상이' 한 단어로만 답하세요.",
        "동일",
    ),
    # ── content — 값의 내용이 있어야 푼다. 마스킹본이 못 푸는 게 정상이다 ──
    Task(
        CONTENT,
        "고객 연락처는 {p} 입니다.",
        "위 연락처의 마지막 네 자리를 숫자만 답하세요.",
        "{p_last}",
    ),
    Task(
        CONTENT,
        "고객 연락처는 {p} 입니다.",
        "위 연락처의 가운데 네 자리를 숫자만 답하세요.",
        "{p_mid}",
    ),
    Task(
        CONTENT,
        "고객 이메일은 {e} 입니다.",
        "위 이메일에서 @ 앞부분만 그대로 답하세요.",
        "{e_local}",
    ),
    Task(
        CONTENT,
        "고객 연락처는 {p} 입니다.",
        "위 연락처를 그대로 다시 적으세요.",
        "{p}",
    ),
)

# 값은 전부 예약 범위·합성이다. §7.4 가 "실제 개인정보를 절대 넣지 않는다" 고 했다.
_PHONES = ("010-1234-5678", "010-2345-6789", "010-3456-7890", "010-4567-8901")
_EMAILS = (
    "minsu.kim@example.test",
    "jiyoung.park@example.test",
    "seojun.lee@example.test",
    "yuna.choi@example.test",
)


@dataclass(frozen=True, slots=True)
class Trial:
    task_id: str
    family: str
    gold: str
    original_prompt: str
    masked_prompt: str


@dataclass(slots=True)
class FamilyTally:
    trials: int = 0
    original_correct: int = 0
    masked_correct: int = 0
    agreement: int = 0

    @property
    def original_accuracy(self) -> float:
        return self.original_correct / self.trials if self.trials else 0.0

    @property
    def masked_accuracy(self) -> float:
        return self.masked_correct / self.trials if self.trials else 0.0

    @property
    def relative_degradation(self) -> float:
        """원문이 0 이면 비교 자체가 성립하지 않는다 — 저하를 0 으로 보고하지 않는다."""
        if not self.original_accuracy:
            return float("nan")
        return 1.0 - (self.masked_accuracy / self.original_accuracy)

    def as_dict(self) -> dict[str, Any]:
        degradation = self.relative_degradation
        return {
            "trials": self.trials,
            "original_accuracy": round(self.original_accuracy, 4),
            "masked_accuracy": round(self.masked_accuracy, 4),
            "relative_degradation": None if degradation != degradation else round(degradation, 4),
            "answer_agreement": round(self.agreement / self.trials, 4) if self.trials else 0.0,
        }


@dataclass(slots=True)
class QualityReport:
    model: str
    families: dict[str, FamilyTally] = field(default_factory=dict)
    unmasked_trials: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def _combined(self, names: Sequence[str]) -> FamilyTally:
        total = FamilyTally()
        for name in names:
            tally = self.families.get(name)
            if tally is None:
                continue
            total.trials += tally.trials
            total.original_correct += tally.original_correct
            total.masked_correct += tally.masked_correct
            total.agreement += tally.agreement
        return total

    @property
    def headline(self) -> FamilyTally:
        """대표 수치 — `content` 를 뺀 것. 뺀 이유는 모듈 docstring 에 있다."""
        return self._combined(
            [name for name in self.families if name not in INTENDED_LOSS_FAMILIES]
        )

    @property
    def intended_loss(self) -> FamilyTally:
        return self._combined([name for name in self.families if name in INTENDED_LOSS_FAMILIES])

    @property
    def within_target(self) -> bool:
        degradation = self.headline.relative_degradation
        return degradation == degradation and degradation <= 0.10

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "spec": "§7.1.1 마스킹 응답 품질 저하 ≤ 0.10 (원문 대비 상대 저하)",
            "headline": self.headline.as_dict(),
            "headline_excludes": sorted(INTENDED_LOSS_FAMILIES),
            "within_target": self.within_target,
            # 대표 수치에서 뺐지만 숨기지는 않는다 — 도구가 제 일을 한 크기다.
            "intended_loss": self.intended_loss.as_dict(),
            "by_family": {name: tally.as_dict() for name, tally in sorted(self.families.items())},
            "trials_without_masking": self.unmasked_trials,
            # 버려진 시행. 분모에서 빠졌으므로 크기를 보여야 수치를 믿을지 판단할 수 있다.
            "dropped_trials": len(self.errors),
            "errors": self.errors[:5],
            "notes": self.notes,
        }


def assert_synthetic_only(text: str) -> None:
    """§7.4 — 실제 개인정보가 API 로 나가는 경로를 만들지 않는다."""
    allowed = set(_PHONES)
    for match in re.finditer(r"01[016789]-\d{3,4}-\d{4}", text):
        if match.group(0) not in allowed:
            raise ValueError(f"합성 값이 아닌 전화번호가 프롬프트에 있다: {match.group(0)}")
    for match in re.finditer(r"[\w.+-]+@[\w.-]+", text):
        if not match.group(0).endswith("example.test"):
            raise ValueError(f"예약 도메인이 아닌 이메일이 프롬프트에 있다: {match.group(0)}")


def build_trials(count: int = 200, *, policy_path: str = "config/policy.yaml") -> list[Trial]:
    """원문/마스킹 두 벌을 만든다. 마스킹은 제품 경로(`Inspector`)를 그대로 쓴다."""
    from garim import Inspector

    inspector = Inspector(policy_path)
    trials: list[Trial] = []
    for index in range(count):
        task = TASKS[index % len(TASKS)]
        phone = _PHONES[index % len(_PHONES)]
        email = _EMAILS[index % len(_EMAILS)]
        slots = {
            "p": phone,
            "p2": _PHONES[(index + 1) % len(_PHONES)],
            "p_mid": phone.split("-")[1],
            "p_last": phone.split("-")[2],
            "e": email,
            "e2": _EMAILS[(index + 1) % len(_EMAILS)],
            "e_local": email.split("@")[0],
        }
        body = task.body.format(**slots)
        result = inspector.check(body)
        masked_body = result.masked_text if result.masked_text is not None else body
        original = f"{body} {task.question}"
        assert_synthetic_only(original)
        trials.append(
            Trial(
                task_id=f"mq-{index:04d}",
                family=task.family,
                gold=task.gold.format(**slots),
                original_prompt=original,
                masked_prompt=f"{masked_body} {task.question}",
            )
        )
    return trials


def _normalize(answer: str) -> str:
    """서식만 걷어낸다. 짧게 자르는 것은 장황한 답이 정답을 우연히 품는 것을 막으려는 것."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", answer).lower()[:40]


def run(trials: Sequence[Trial], client: Client, *, model: str) -> QualityReport:
    if not trials:
        raise ValueError("mask-quality run needs at least one trial")
    report = QualityReport(model=model)
    for trial in trials:
        if trial.original_prompt == trial.masked_prompt:
            report.unmasked_trials += 1
        # 두 팔 중 하나라도 실패하면 그 시행을 통째로 버린다. 한 팔만 살리면 짝이
        # 깨져 대조가 아니게 된다. 200 번째에서 죽어 전부 날리는 것도 막는다.
        try:
            original = _normalize(client(trial.original_prompt))
            masked = _normalize(client(trial.masked_prompt))
        except RuntimeError as exc:
            report.errors.append(f"{trial.task_id}: {exc}")
            continue
        tally = report.families.setdefault(trial.family, FamilyTally())
        gold = _normalize(trial.gold)
        tally.trials += 1
        tally.original_correct += gold in original
        tally.masked_correct += gold in masked
        tally.agreement += original == masked
    if report.unmasked_trials:
        report.notes.append(
            f"{report.unmasked_trials} trials had nothing to mask — the contrast is vacuous there"
        )
    if report.errors:
        report.notes.append(f"{len(report.errors)}/{len(trials)} trials dropped on API errors")
    return report


def masked_label_coverage(trials: Sequence[Trial]) -> set[str]:
    """어떤 라벨이 실제로 가려졌는가.

    ★ L1 가중치가 없으면 `PERSON`·`ADDRESS` 가 안 가려진다. 그러면 이 지표는 "전화번호와
    이메일을 가린 것의 영향" 을 재는 셈이라 §7.1.1 이 뜻한 것보다 **약한 대조**다.
    수치와 함께 반드시 적어야 오해가 없다.
    """
    return {
        match.group(1)
        for trial in trials
        for match in re.finditer(r"<([A-Z_]+)_\d+>", trial.masked_prompt)
    }


# ── API 클라이언트. 의존성을 늘리지 않으려고 stdlib 로 부른다. ──────────


def load_env(path: str | Path = ".env") -> dict[str, str]:
    """`.env` 를 읽는다. 값은 절대 로그로 내보내지 않는다."""
    source = Path(path)
    if not source.is_file():
        return {}
    values: dict[str, str] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _ssl_context() -> ssl.SSLContext:
    """macOS 의 python.org 빌드는 시스템 루트 인증서를 안 쓴다 — certifi 가 있으면 쓴다.

    검증을 끄는 선택지는 두지 않는다. 이 하네스는 API 키를 들고 나가므로 평문 강등이
    가능한 경로를 만들면 안 된다.
    """
    try:
        import certifi
    except ModuleNotFoundError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _post(
    url: str, payload: dict[str, Any], headers: dict[str, str], *, retries: int = 5
) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - 벤치 전용, 고정 호스트
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=90, context=_ssl_context()
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 429/5xx 는 물러섰다 다시 친다. 나머지는 즉시 올린다 — 키·모델 오류를
            # 재시도로 덮으면 원인이 안 보인다.
            if exc.code not in {429, 500, 502, 503, 504} or attempt == retries - 1:
                raise RuntimeError(f"API {exc.code}: {exc.read()[:160]!r}") from exc
            # 분당 한도는 1·2·4초로는 안 풀린다 — 한 분을 넘기도록 물러선다.
            time.sleep(min(45, 5 * 2**attempt))
    raise RuntimeError("unreachable")


def gemini_client(api_key: str, *, model: str = "gemini-3.5-flash") -> Client:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def client(prompt: str) -> str:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 2048},
        }
        body = _post(url, payload, {"x-goog-api-key": api_key})
        try:
            parts = body["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError):
            return ""

    return client


def openai_client(api_key: str, *, model: str = "gpt-4o-mini") -> Client:
    url = "https://api.openai.com/v1/chat/completions"

    def client(prompt: str) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        body = _post(url, payload, {"Authorization": f"Bearer {api_key}"})
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            return ""

    return client


_DEFAULT_MODEL = {"gemini": "gemini-3.5-flash", "openai": "gpt-4o-mini"}


def build_client(provider: str, env: dict[str, str], model: str) -> Client | None:
    if provider == "gemini":
        key = env.get("GEMINI_API_KEY")
        return gemini_client(key, model=model) if key else None
    key = env.get("OPENAI_API_KEY")
    return openai_client(key, model=model) if key else None


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="§8.1-13b 마스킹 응답 품질")
    parser.add_argument("--provider", choices=("gemini", "openai"), required=True)
    parser.add_argument("--model")
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--output")
    args = parser.parse_args()

    env = {**load_env(args.env), **os.environ}
    model = args.model or _DEFAULT_MODEL[args.provider]
    if args.provider == "gemini":
        # §7.4 — free tier 는 데이터가 학습에 쓰인다. 코드가 tier 를 알 수 없다.
        print("⚠️ §7.4: Gemini 는 유료 tier 로 실행해야 한다 (free tier 는 학습에 쓰인다).")
    client = build_client(args.provider, env, model)
    if client is None:
        print(f"{args.provider} API 키가 없다 — .env 또는 환경변수를 확인한다.")
        return 2

    trials = build_trials(args.trials)
    covered = masked_label_coverage(trials)
    report = run(trials, client, model=f"{args.provider}:{model}")
    report.notes.append(f"masked labels present: {sorted(covered) or ['none']}")
    if "PERSON" not in covered:
        report.notes.append(
            "PERSON/ADDRESS are L1-only and unmasked here — weaker contrast than §7.1.1 intends"
        )
    payload = report.as_dict()

    head = payload["headline"]
    print(f"\n{payload['model']} · n={sum(t['trials'] for t in payload['by_family'].values())}")
    for name, tally in payload["by_family"].items():
        degradation = tally["relative_degradation"]
        shown = "n/a" if degradation is None else f"{degradation:+.3f}"
        marker = " (대표 수치에서 제외)" if name in payload["headline_excludes"] else ""
        print(
            f"  {name:<12} n={tally['trials']:<4} 원문 {tally['original_accuracy']:.3f} · "
            f"마스킹 {tally['masked_accuracy']:.3f} · 저하 {shown}{marker}"
        )
    print(
        f"\n대표 저하 {head['relative_degradation']:+.4f} (§7.1.1 목표 ≤ 0.10) → "
        f"{'통과' if payload['within_target'] else '미달'}"
    )
    loss = payload["intended_loss"]
    if loss["trials"]:
        print(f"의도된 손실 {loss['relative_degradation']:+.4f} — 가려서 못 푸는 것, 결함 아님")
    for note in payload["notes"]:
        print(f"  ⚠️ {note}")
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"-> {args.output}")
    return 0 if payload["within_target"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
