# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Purchase products: every cart item now points to a product (`product_id`), found by its normalized name (case, accents and plurals ignored, so "Limones" is "Limon") or created on the fly. A product's category is copied to all of its items, so categorizing it once covers every purchase. New endpoints `GET /api/v1/purchase/products` (with times bought, prices and last store), `PUT /api/v1/purchase/products/{id}`, `POST /api/v1/purchase/products/{id}/merge`, and `POST /api/v1/purchase/products/link-items` to group the items bought before this change. `POST /api/v1/purchase/carts/{id}/items` accepts an optional `product_id`, and `PUT` on an item can clear its category with `category_id: null`. Needs `sql/2026-10-04-purchase-products.sql` run on the database before deploying. ([#43](https://github.com/joaquin-p-olivera/finview-backend/pull/43))
- MIT license (`LICENSE`). ([#42](https://github.com/joaquin-p-olivera/finview-backend/pull/42))
- `GET /api/v1/stats/statement-report?statement_id=` returns one confirmed statement (the latest by default) broken down by currency and category, with the same numbers as the monthly Itaú report email: signed totals per category, plus the bank's official total and the other charges (insurance, interest, fees) when the statement has them. `POST /api/v1/statements/external` accepts an optional `summary` with those official totals and stores it in `statements.raw_json`. Stats endpoints accept `?currency=UYU|USD` so pesos and dollars are no longer summed together. ([#41](https://github.com/joaquin-p-olivera/finview-backend/pull/41))
- Per-user list of supermarkets for carts: `GET/POST /api/v1/purchase/stores` and `PUT/DELETE /api/v1/purchase/stores/{id}`. `POST /api/v1/purchase/carts` now takes a `store_id` from that list or a `store_name`, which is matched ignoring case and added to the list when new, so every cart of the same store has the same name. Renaming a store renames its carts; deleting it keeps their name. Needs `sql/2026-10-03-purchase-stores.sql` run on the database before deploying. ([#39](https://github.com/joaquin-p-olivera/finview-backend/pull/39))

### Removed

- `Keep API awake` workflow: GitHub dropped most of its scheduled runs (about one every 3-4 hours instead of every 10 minutes), so the `/health` pings now come from an external cron service (cron-job.org) with the same Friday to Sunday 09:00-21:00 Uruguay schedule. ([#40](https://github.com/joaquin-p-olivera/finview-backend/pull/40))

## [1.3.0] - 3 Oct 2026

### Added

- `POST /api/v1/purchase/carts/{cart_id}/items` accepts an optional client-generated `id` (UUID). Sending the same id again returns the item already stored instead of adding it twice (409 if that id belongs to another cart). Unlike the in-memory `Idempotency-Key` cache, this also holds across API restarts, so the frontend can queue cart changes made without signal and retry them later. ([#35](https://github.com/joaquin-p-olivera/finview-backend/pull/35))

### Fixed

- The app no longer fails to start with SQLAlchemy 2.1 (`No module named 'psycopg'`): bare `postgresql://` database URLs are now pinned to the installed `psycopg2` driver, since SQLAlchemy 2.1 switched the default to psycopg 3. ([#34](https://github.com/joaquin-p-olivera/finview-backend/pull/34))

### Changed

- Bump `SQLAlchemy` from 2.0.52 to 2.1.1. ([#21](https://github.com/joaquin-p-olivera/finview-backend/pull/21))
- Bump `openai` requirement from `>=1.0.0` to `>=3.19.2`. ([#18](https://github.com/joaquin-p-olivera/finview-backend/pull/18))
- Bump `bcrypt` requirement from `>=4.0.1` to `>=5.0.0`; passwords are now explicitly truncated to bcrypt's 72-byte limit, since bcrypt 5 raises on longer ones instead of truncating them, so login and sign-up with long passwords keep working. ([#14](https://github.com/joaquin-p-olivera/finview-backend/pull/14))

## [1.2.0] - 30 Sep 2026

### Added

- `Keep API awake` workflow (`.github/workflows/keep-alive.yml`) that pings `/health` every 10 minutes on Fridays, Saturdays and Sundays from 09:00 to 21:00 (Uruguay time) so the Render free instance doesn't sleep, and `/health` now also answers `HEAD`. ([#29](https://github.com/joaquin-p-olivera/finview-backend/pull/29))
- Writes (`POST`, `PUT`, `PATCH`, `DELETE`) accept an `Idempotency-Key` header: a retry with the same key within 10 minutes gets the original response instead of running again, so the frontend can safely retry a write whose response was lost (e.g. adding a product to a cart). ([#29](https://github.com/joaquin-p-olivera/finview-backend/pull/29))

### Fixed

- A request no longer fails with a 500 when its pooled database connection was closed while the API sat idle: the database engine now checks pooled connections before use (`pool_pre_ping`), recycles them, uses a connect timeout and TCP keepalives. ([#29](https://github.com/joaquin-p-olivera/finview-backend/pull/29))

## [1.1.1] - 30 Sep 2026

### Fixed

- `GET /api/v1/purchase/stats` no longer fails with a 500 (`text` was used without being imported in the by-month query), so purchase stats show real data again; months are now returned oldest first. ([#28](https://github.com/joaquin-p-olivera/finview-backend/pull/28))

## [1.1.0] - 30 Sep 2026

### Changed

- The production deploy workflow (`Deploy to Render`, `.github/workflows/deploy.yml`) is now manual-only: it no longer runs on every push to `master` and is started by hand from the Actions tab (`workflow_dispatch`), as in trip-trace-api. ([#25](https://github.com/joaquin-p-olivera/finview-backend/pull/25))
- Bumped `pydantic` from 2.13.4 to 2.13.5. ([#17](https://github.com/joaquin-p-olivera/finview-backend/pull/17))
- Bumped `uvicorn` from 0.52.4 to 0.54.0. ([#16](https://github.com/joaquin-p-olivera/finview-backend/pull/16))
- Bumped `alembic` from 1.19.1 to 1.20.0. ([#19](https://github.com/joaquin-p-olivera/finview-backend/pull/19))
- Bumped `psycopg2-binary` from 2.9.12 to 2.9.13. ([#20](https://github.com/joaquin-p-olivera/finview-backend/pull/20))
- Bumped `pymysql` from 1.2.0 to 1.2.3. ([#22](https://github.com/joaquin-p-olivera/finview-backend/pull/22))
- Raised the minimum `pillow` version from 10.0.0 to 12.3.0. ([#15](https://github.com/joaquin-p-olivera/finview-backend/pull/15))

## [1.0.0] - 30 Sep 2026

### Added

- CI: every PR to `develop` or `master` now runs `API Checks / check` (`.github/workflows/checks.yml`) — installs `requirements.txt` on Python 3.12, compiles every module and imports `app.main` with dummy `DATABASE_URL`/`SECRET_KEY`, so a broken import or dependency fails the PR instead of the deploy. Also runnable by hand, and reused by the deploy workflow. ([#13](https://github.com/joaquin-p-olivera/finview-backend/pull/13))
- Configured Dependabot (pip + github-actions), monthly, opening its PRs against `develop`. ([#13](https://github.com/joaquin-p-olivera/finview-backend/pull/13))
- Added an automatic backport: when `master` gets something `develop` doesn't have (e.g. a hotfix merged straight to `master`), a PR bringing it back into `develop` is opened (`.github/workflows/backport-to-develop.yml`). ([#13](https://github.com/joaquin-p-olivera/finview-backend/pull/13))

### Changed

- Deploys to Render now go through GitHub Actions (`.github/workflows/deploy.yml`) instead of Render's own auto-deploy: every push to `master` runs the checks first and, only if they pass, deploys that exact commit through Render's deploy hook (`ref` parameter). It can also be run by hand (`workflow_dispatch`) to deploy any branch's current commit — deploying anything other than `master` requires confirming with `confirm_non_master: 'yes'`, a guard against an accidental non-master deploy to production. Requires a `RENDER_DEPLOY_HOOK_URL` repo secret and disabling Render's Auto-Deploy setting (both set up outside this repo). ([#13](https://github.com/joaquin-p-olivera/finview-backend/pull/13))
