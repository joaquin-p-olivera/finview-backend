# Finview Backend

FastAPI backend for **Finview**, a personal finance app: parses bank credit
card statements (PDF) with AI into categorized transactions, and separately
tracks supermarket shopping (carts and lists).

## Tech stack

- **FastAPI** + **Uvicorn**
- **SQLAlchemy** (MySQL) + **Alembic** for migrations
- **JWT** auth (`python-jose`)
- AI parsing with **Claude** (`anthropic`): the statement PDF is sent as-is and
  never stored; password-protected PDFs are opened with `pypdf`

## Quick start

```bash
# 1. Copy the dev env template and fill in real values (DB URL, API keys, etc.)
cp .env.development .env

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run migrations
alembic upgrade head

# 4. Start the dev server
uvicorn app.main:app --reload --port 8000
```

API docs (Swagger UI) are then available at `http://localhost:8000/docs`.

See [AGENTS.md](./AGENTS.md) for the full environment variable reference,
project structure, and conventions.

## Main endpoint groups

All routes are under `/api/v1`.

| Prefix | What it does |
|---|---|
| `/auth` | Register, login (OAuth2 password flow, returns a JWT) |
| `/statements` | Upload a PDF statement, poll parse status, review/confirm transactions, or `/statements/external` for trusted automated imports (skips the review step) |
| `/transactions` | List (with filters/pagination) and delete transactions |
| `/categories` | CRUD for expense categories |
| `/stats` | Dashboard aggregates — summary, by-month, by-category, by-bank, top-merchants, trends. Several accept `?period=all|latest` to scope to the most recently confirmed statement's date range, and all accept `?currency=UYU|USD`. `statement-report` gives one statement's breakdown by currency and category |
| `/purchase` | Independent module: shopping carts, shopping lists, purchase categories, purchase stats |

## Deployment

Deployed on Render. Copy `.env.production` to `.env` and fill in real values
before deploying; see AGENTS.md for what each variable does.
