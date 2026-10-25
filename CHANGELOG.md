# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- CodeQL code scanning workflow and Dependabot configuration (pip, Docker, GitHub Actions).
- API reference and terminal screenshots in the README.
- Roadmap tracked as GitHub issues under the `v0.2.0` milestone.

### Changed

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
