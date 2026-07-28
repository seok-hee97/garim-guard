# SafeAgent-KR — 실행 서비스(HTTP 런타임) 컨테이너 (core 런타임만; GPU 레일 제외).
# 빌드:  docker build -t safeagent-kr .
# 실행:  docker run -p 8000:8000 -e GEMINI_API_KEY=... safeagent-kr
# ⚠️ 이 이미지는 core.lock 만 설치 → provider=gemini 전용(google-genai 포함, openai 미포함).
#    OpenAI provider 로 쓰려면 requirements.core.txt 에 openai 를 추가하고 core.lock 재생성 후 재빌드.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
# ⚠️ APP_ENV 는 이미지에 고정하지 않는다 — 미설정이면 데모 키로 부팅(빠른 데모).
#    ★프로덕션 배포는 APP_ENV=prod + SAFEAGENT_API_KEYS(주입 자격증명)를 오케스트레이터가 주입★
#    (prod 인데 SAFEAGENT_API_KEYS 없으면 데모 키를 거부하고 부팅 실패 — 의도된 fail-closed).

WORKDIR /app

# 의존성 먼저(레이어 캐시). ★재현 빌드: 핀 고정 core.lock★ — presidio/torch 는 서비스 런타임에 불필요.
#   *.txt(>= 범위)가 아니라 *.lock(== 고정)을 써 미래 버전 드리프트로 빌드가 조용히 깨지지 않게.
COPY requirements.core.lock ./
RUN pip install --no-cache-dir -r requirements.core.lock

# 앱 소스만 복사(.dockerignore 로 .venv/.git/results/data/models 제외).
COPY src ./src
COPY config ./config

# ★비루트 실행★ — 컨테이너 탈취 시 권한 최소화. 앱 파일은 읽기전용(소유권만 이전).
RUN useradd --system --uid 10001 --no-create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 헬스체크 — /healthz 는 의존성 무관 200.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

# prod: 오케스트레이터가 GEMINI_API_KEY/OPENAI_API_KEY 를 env 로 주입(.env 미신뢰).
CMD ["uvicorn", "src.agent.server:app", "--host", "0.0.0.0", "--port", "8000"]
