# prompt2midi 서버 이미지. 키는 이미지에 넣지 않는다 — 실행 후 /admin 에서 등록한다.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    P2M_DATA_DIR=/data

# 비루트 사용자
RUN groupadd --system --gid 10001 p2m \
 && useradd --system --uid 10001 --gid p2m --no-create-home --shell /usr/sbin/nologin p2m \
 && mkdir -p /app /data \
 && chown p2m:p2m /data

WORKDIR /app
# 정적 화면(web/)은 같은 프로세스가 서빙하므로 /app 아래에 함께 둔다.
COPY pyproject.toml LICENSE README.md ./
COPY src ./src
COPY schemas ./schemas
COPY web ./web
RUN pip install .

# 관리자 설정·API 키(secrets.json)가 저장되는 곳. 볼륨으로 마운트해 재시작 뒤에도 유지한다.
VOLUME ["/data"]
USER p2m
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "prompt2midi.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
