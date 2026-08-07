#!/usr/bin/env python3
"""§5.2 라이선스 게이트 — HuggingFace 를 런타임 조회해 매니페스트와 대조한다.

`validate_models.py`·`validate_sources.py` 는 **정적 검사**다. 파일에 적힌 문자열이
서로 맞는지만 본다. 실제 라이선스 확인은 `download_assets.py --execute` 안에서만
돌았고, 그래서 자산을 받기 전에는 "적어둔 라이선스가 지금도 맞는가" 를 확인할 방법이
없었다. §8.1-15c 가 이 스크립트를 요구한 이유다.

    python train/license_check.py            # 활성 모델·소스 전부
    python train/license_check.py --include-disabled --include-baselines

네트워크가 필요하므로 CI 기본 잡에 넣지 않는다. 게이트가 실패하면 종료 코드 1 이다.
"""

from __future__ import annotations

import argparse
import json
import sys

from garim.training.models import load_model_registry, resolve_huggingface_model
from garim.training.sources import load_source_registry, resolve_huggingface_license


def _check_models(registry, *, include_disabled: bool) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for asset in registry.manifest.models:
        if not asset.enabled and not include_disabled:
            continue
        row: dict[str, object] = {"kind": "model", "name": asset.name, "purpose": asset.purpose}
        try:
            license_value, gated, parameters = resolve_huggingface_model(
                asset.model_id, asset.revision
            )
        except Exception as exc:  # 네트워크·삭제·gated 전부 여기로 온다
            results.append({**row, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
            continue
        allowed = registry.allowed_licenses_for(asset.purpose)
        problems = []
        if license_value.lower() != asset.license.lower():
            problems.append(f"license drift: manifest {asset.license!r} vs hub {license_value!r}")
        if license_value.lower() not in allowed:
            problems.append(f"{license_value!r} not allowed for purpose {asset.purpose!r}")
        if gated != asset.gated:
            problems.append(f"gated drift: manifest {asset.gated} vs hub {gated}")
        if parameters != asset.parameters:
            problems.append(f"parameter drift: {asset.parameters} vs {parameters}")
        results.append(
            {**row, "ok": not problems, "license": license_value, "detail": problems or "ok"}
        )
    return results


def _check_sources(registry, *, include_disabled: bool) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    allowed = {item.lower() for item in registry.manifest.allowed_licenses}
    for source in registry.manifest.sources:
        if not source.enabled and not include_disabled:
            continue
        row: dict[str, object] = {"kind": "dataset", "name": source.name, "use": source.use}
        if source.hf_id is None:
            # HF 밖 소스는 조회할 곳이 없다 — 매니페스트 선언만 허용 목록과 대조한다.
            declared = (source.license or "").lower()
            ok = declared in allowed
            results.append(
                {**row, "ok": ok, "license": source.license, "detail": "declared (not on hub)"}
            )
            continue
        try:
            license_value = resolve_huggingface_license(source.hf_id, source.revision)
        except Exception as exc:
            results.append({**row, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
            continue
        problems = []
        if source.license and license_value.lower() != source.license.lower():
            problems.append(f"license drift: manifest {source.license!r} vs hub {license_value!r}")
        if license_value.lower() not in allowed:
            problems.append(f"{license_value!r} is not in allowed_licenses")
        results.append(
            {**row, "ok": not problems, "license": license_value, "detail": problems or "ok"}
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-manifest", default="train/models.yaml")
    parser.add_argument("--sources-manifest", default="train/sources.yaml")
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    models = load_model_registry(args.models_manifest)
    sources = load_source_registry(args.sources_manifest)
    results = _check_models(models, include_disabled=args.include_disabled) + _check_sources(
        sources, include_disabled=args.include_disabled
    )
    ok = all(item["ok"] for item in results)
    if args.json:
        print(json.dumps({"ok": ok, "checks": results}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            mark = "OK  " if item["ok"] else "FAIL"
            print(f"{mark} {item['kind']:8} {item['name']:34} {item.get('detail')}")
        print(f"\n{'통과' if ok else '실패'} — {len(results)}건 조회")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
