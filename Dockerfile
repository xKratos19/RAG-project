FROM python:3.12-slim@sha256:971f04b358cf483ec4455e59df1b2563e4ea22ad10903f0c2850d4f11743c696 AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
COPY openapi.yaml ./openapi.yaml
RUN pip install --upgrade pip && pip wheel --wheel-dir /wheels .

FROM python:3.12-slim@sha256:971f04b358cf483ec4455e59df1b2563e4ea22ad10903f0c2850d4f11743c696 AS runtime

RUN useradd --uid 1000 --create-home appuser
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY app ./app
COPY openapi.yaml ./openapi.yaml
COPY README.md ./README.md

USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/v1/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
