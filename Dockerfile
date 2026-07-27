# SafeAgent-KR — 실행 서비스(HTTP 런타임) 컨테이너 (core 런타임만; GPU 레일 제외).
# 빌드:  docker build -t safeagent-kr .
# 실행:  docker run -p 8000:8000 -e GEMINI_API_KEY=... safeagent-kr
#        (또는 -e OPENAI_API_KEY=... 후 policy.yaml provider.active=openai)

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=prod

WORKDIR /app

# 의존성 먼저(레이어 캐시). core 만 — presidio/torch 는 서비스 런타임에 불필요.
COPY requirements.core.txt ./
RUN pip install --no-cache-dir -r requirements.core.txt

# 앱 소스만 복사(.dockerignore 로 .venv/.git/results/data/models 제외).
COPY src ./src
COPY config ./config

EXPOSE 8000

# 헬스체크 — /healthz 는 의존성 무관 200.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

# prod: 오케스트레이터가 GEMINI_API_KEY/OPENAI_API_KEY 를 env 로 주입(.env 미신뢰).
CMD ["uvicorn", "src.agent.server:app", "--host", "0.0.0.0", "--port", "8000"]
