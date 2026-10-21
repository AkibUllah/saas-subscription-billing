#!/usr/bin/env bash
# Codespaces start hook: bring up API + scheduler + PostgreSQL, seed demo data and run billing once.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose up -d --build

echo "waiting for the API on :8000 ..."
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# `billing seed` is idempotent on plan codes, coupon codes and customer emails.
docker compose exec -T api billing seed
docker compose exec -T api billing run

cat <<MSG

SaaS Subscription Billing is up.
  API docs:   forwarded port 8000, path /docs   (X-API-Key: ${API_KEY:-change-me})
  Seeded:     Starter / Team / Business (yearly) plans, LAUNCH20 + WELCOME500 coupons, five customers
  Try:        GET /v1/plans, POST /v1/subscriptions, POST /v1/billing/run?as_of=<ISO timestamp>, GET /v1/metrics
MSG
