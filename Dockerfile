# syntax=docker/dockerfile:1.6
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/billing

COPY pyproject.toml requirements.txt README.md ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY saas_billing ./saas_billing
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
RUN pip install --no-deps -e .

RUN addgroup --system billing && adduser --system --ingroup billing billing && chown -R billing:billing /srv/billing
USER billing

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# Migrations run first so a fresh PostgreSQL volume is ready before the API starts.
# SEED_DEMO_ON_START=true also loads the idempotent demo catalogue (used by the Render blueprint).
CMD ["sh", "-c", "alembic upgrade head && if [ \"$SEED_DEMO_ON_START\" = true ]; then billing seed; fi && uvicorn saas_billing.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
