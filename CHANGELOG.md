# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- CI: every PR to `develop` or `master` now runs `API Checks / check` (`.github/workflows/checks.yml`) — installs `requirements.txt` on Python 3.12, compiles every module and imports `app.main` with dummy `DATABASE_URL`/`SECRET_KEY`, so a broken import or dependency fails the PR instead of the deploy. Also runnable by hand, and reused by the deploy workflow. ([#13](https://github.com/joaquin-p-olivera/finview-backend/pull/13))
- Configured Dependabot (pip + github-actions), monthly, opening its PRs against `develop`. ([#13](https://github.com/joaquin-p-olivera/finview-backend/pull/13))
- Added an automatic backport: when `master` gets something `develop` doesn't have (e.g. a hotfix merged straight to `master`), a PR bringing it back into `develop` is opened (`.github/workflows/backport-to-develop.yml`). ([#13](https://github.com/joaquin-p-olivera/finview-backend/pull/13))

### Changed

- Deploys to Render now go through GitHub Actions (`.github/workflows/deploy.yml`) instead of Render's own auto-deploy: every push to `master` runs the checks first and, only if they pass, deploys that exact commit through Render's deploy hook (`ref` parameter). It can also be run by hand (`workflow_dispatch`) to deploy any branch's current commit — deploying anything other than `master` requires confirming with `confirm_non_master: 'yes'`, a guard against an accidental non-master deploy to production. Requires a `RENDER_DEPLOY_HOOK_URL` repo secret and disabling Render's Auto-Deploy setting (both set up outside this repo). ([#13](https://github.com/joaquin-p-olivera/finview-backend/pull/13))
