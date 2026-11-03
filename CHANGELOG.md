# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Scheduled subscription changes (#4): `pause`, `resume`, `change_plan` and `change_seats` at the period end or at a given instant, via `POST|GET /v1/subscriptions/{id}/scheduled-changes` and `DELETE …/scheduled-changes/{change_id}`. The billing run applies due changes before invoicing: plan and seat changes on the boundary bill the next period on the new values without proration, mid-period ones are prorated; paused subscriptions are not invoiced and a resume after a period-end pause starts a fresh period. One pending change per action, conflicts rejected with `409`, invalid changes marked `failed`, `subscription.change_scheduled|change_canceled|change_failed` webhooks.
- Alembic migration `0002` adding the `scheduled_changes` table.
- CodeQL code scanning workflow and Dependabot configuration (pip, Docker, GitHub Actions).
- API reference and terminal screenshots in the README.
- Roadmap tracked as GitHub issues under the `v0.2.0` milestone.

### Changed

- Canceling a subscription also cancels its pending scheduled changes.
- CI workflow runs with a read-only `GITHUB_TOKEN` by default.

## [0.1.0] - 2026-10-25

First public release.

### Added

- Customers, plans (monthly/yearly, flat + per-seat, metered add-ons), coupons and trials.
- Subscription lifecycle (`trialing → active → past_due → canceled | unpaid`, plus pause/resume) enforced by a transition table with status events.
- Prorated mid-cycle plan and seat changes, pending or invoiced immediately; interval changes reset the billing anchor.
- Period invoices with plan, seat, usage, proration, coupon, credit and VAT lines; yearly numbering; JSON and HTML renderings.
- `PaymentGateway` protocol with a deterministic `MockGateway` and labelled Stripe-like / bKash-like stubs.
- Dunning retries at 1, 3, 5 and 7 days, then write-off and `unpaid`; recovery on later payment.
- Idempotent billing run (API and CLI) that rolls periods over, catches up, collects and runs dunning.
- HMAC-signed outgoing webhooks stored before delivery with attempt history.
- Metrics: MRR, ARR, churn, trial conversion, invoiced and collected totals.
- Alembic migrations, SQLite for dev/tests, PostgreSQL via docker compose, admin API key, CI on Python 3.10/3.11, Pages API reference, Codespaces and Render kits.

### Fixed

- `postgres://` and `postgresql://` database URLs are rewritten to the psycopg 3 driver.

[Unreleased]: https://github.com/AkibUllah/saas-subscription-billing/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AkibUllah/saas-subscription-billing/releases/tag/v0.1.0
