# AGENTS.md - Finview Backend

## Project Overview

FastAPI-based backend for Finview expense tracking app. Provides REST API for expense management and purchase tracking (shopping cart + shopping lists).

## Relationship with Frontend

- **Frontend URL**: `http://localhost:5173`
- **Frontend Repo**: Separate repo (`finview-frontend`)
- **API Base**: `/api/v1`
- **CORS**: Currently configured for `localhost:5173` and `localhost:3000`

## Commands

```bash
# Development
cp .env.development .env
uvicorn app.main:app --reload --port 8000

# Production
cp .env.production .env
# Edit .env with production values
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Environment Variables

Create a `.env` file (copy from `.env.development` or `.env.production`).

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | MySQL connection string | Yes |
| `SECRET_KEY` | JWT secret key | Yes |
| `CORS_ORIGINS` | Comma-separated list of allowed origins | No |
| `GEMINI_API_KEY` | Google Gemini API key | No |
| `GROQ_API_KEY` | Groq API key | No |
| `UPLOAD_DIR` | Directory for file uploads | No |
| `MAX_FILE_SIZE_MB` | Max file size in MB | No |
| `EXTERNAL_IMPORT_SECRET` | Shared secret required (as `X-External-Import-Key` header) to call `POST /api/v1/statements/external` | No |
| `EXTERNAL_IMPORT_ALLOWED_EMAIL` | Only this account can use `POST /api/v1/statements/external` | No |

### Environment Files

- `.env.default` - Template with all variables (committed to repo)
- `.env.development` - Local development values (gitignored)
- `.env.production` - Production template with empty values (committed to repo)

**Setup for development:**
```bash
cp .env.development .env
```

## Project Structure

```
app/
├── main.py           # FastAPI app entry point, CORS config, router registration
├── config.py         # Configuration (DB URL, etc.)
├── database.py       # SQLAlchemy engine and session
├── dependencies.py   # Auth dependencies (get_current_user)
├── models/          # SQLAlchemy models
│   ├── user.py
│   ├── purchase.py   # Purchase (cart, lists, categories)
│   └── ...
├── routers/          # API endpoints
│   ├── auth.py
│   ├── purchase.py   # Purchase module endpoints
│   └── ...
├── schemas/         # Pydantic schemas (request/response models)
│   └── purchase.py
└── services/        # Business logic
```

## Database

- **Engine**: MySQL (local)
- **ORM**: SQLAlchemy
- **Migrations**: Alembic (see `alembic/versions/`)

### Key Tables

| Table | Description |
|-------|-------------|
| `users` | User accounts |
| `purchase_carts` | Shopping carts (active/completed) |
| `purchase_cart_items` | Items in carts |
| `purchase_lists` | Shopping lists (planning) |
| `purchase_list_items` | Items in lists |
| `purchase_categories` | Categories for purchases |
| `purchase_stores` | User's list of supermarkets; `purchase_carts.store_id` points here and `store_name` keeps a copy of the name |

There is no `create_all` at startup. Schema changes are SQL scripts in `sql/`, run by hand on Postgres (Render) before deploying the code that needs them.

## Core API Endpoints

All routes are under `/api/v1`. This covers everything outside the Purchase
module (see below for that).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/register` | Create a user |
| POST | `/auth/login` | OAuth2 password flow (form-encoded `username`/`password`, `username` holds the email); returns `{access_token, token_type}` |
| GET | `/auth/me` | Current user |
| POST | `/statements/` | Upload a PDF statement (multipart); creates a `Statement`, parses it in the background, status starts as `processing` |
| GET | `/statements/` | List the user's statements |
| GET | `/statements/{id}/status` | Poll parse status (`processing` → `pending_review` or `error`) |
| GET | `/statements/{id}` | Get parsed transactions for review (requires `pending_review`) |
| POST | `/statements/{id}/confirm` | Save reviewed/edited transactions as confirmed |
| DELETE | `/statements/{id}` | Delete a statement and its file |
| GET | `/statements/{id}/pdf` | Download the original PDF |
| POST | `/statements/external` | Trusted external import — accepts an already-parsed statement as JSON (`category_name` per transaction, not `category_id`) and saves it directly as `confirmed`, skipping upload and review. Requires `X-External-Import-Key` header matching `EXTERNAL_IMPORT_SECRET`, and only works for the account in `EXTERNAL_IMPORT_ALLOWED_EMAIL`. Built for the Apps Script automation, not the web app. |
| GET | `/transactions/` | List transactions (filters + pagination) |
| DELETE | `/transactions/{id}` | Delete a transaction |
| GET | `/categories/` | List the user's categories |
| POST | `/categories/` | Create a category |
| PUT | `/categories/{id}` | Update a category |
| DELETE | `/categories/{id}` | Delete a category |
| POST | `/categories/seed` | Bulk-create a default set of categories |
| GET | `/stats/summary` | Totals: transaction count, current/previous month spend, categories/statements count |
| GET | `/stats/by-month?months=N` | Spend grouped by calendar month |
| GET | `/stats/by-category?period=all\|latest` | Spend grouped by category. `latest` scopes to the date range of the most recently confirmed statement (by `period_end`) instead of all-time |
| GET | `/stats/by-bank?period=all\|latest` | Spend grouped by bank; same `period` semantics |
| GET | `/stats/top-merchants?limit=N&period=all\|latest` | Top merchants by spend; same `period` semantics |
| GET | `/stats/trends?days=N` | Daily totals for the last N days (from today's date, not from the latest transaction — days with no transactions simply don't appear, they aren't zero-filled) |

## Purchase Module (Módulo de Compras)

Independent from expense tracking. Uses `purchase_` prefix for all tables.

### Business Logic (shared with Frontend)

1. **Shopping Cart**: Only 1 active cart at a time per user
2. **Shopping Lists**: User can have N lists for pre-shopping planning
3. **Categories**: Independent from expense categories, manually created
4. **Stores**: Each cart belongs to a supermarket from the user's editable list
5. **Flow**: Add items from list → checkbox prompts for price/quantity → adds to cart

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/purchase/categories` | List categories |
| POST | `/purchase/categories` | Create category |
| GET | `/purchase/stores` | List the user's supermarkets, most used first |
| POST | `/purchase/stores` | Create a supermarket (names are unique per user, ignoring case) |
| PUT | `/purchase/stores/{id}` | Rename a supermarket; also renames `store_name` on its carts |
| DELETE | `/purchase/stores/{id}` | Delete a supermarket; its carts keep their `store_name` and get `store_id = NULL` |
| GET | `/purchase/carts` | List carts (with pagination) |
| GET | `/purchase/carts/active` | Get active cart |
| POST | `/purchase/carts` | Create cart with `store_id` (from the list) or `store_name` (matched against the list ignoring case, and added to it if new) |
| GET | `/purchase/carts/{id}` | Get cart details |
| POST | `/purchase/carts/{id}/items` | Add item to cart. Accepts an optional client-generated `id` (UUID): re-sending the same id returns the existing item instead of adding it twice, so the frontend can queue adds made without signal and retry them safely |
| POST | `/purchase/carts/{id}/complete` | Complete cart |
| GET | `/purchase/lists` | List shopping lists |
| POST | `/purchase/lists` | Create shopping list |
| GET | `/purchase/lists/{id}` | Get list details |
| POST | `/purchase/lists/{id}/items/{item_id}/add-to-cart/{cart_id}` | Add single item to cart |
| GET | `/purchase/stats` | Get purchase statistics |

## Naming Conventions

- **Models**: `PascalCase` (e.g., `PurchaseCart`)
- **Schemas**: `PascalCase` with `Schema` suffix (e.g., `PurchaseCartCreate`)
- **Routers**: `snake_case` (e.g., `purchase.py`)
- **Database tables**: `snake_case` with prefixes (e.g., `purchase_carts`)
- **UUIDs**: Use `UUID` type, store as strings

## Auth

- JWT-based authentication
- Dependencies: `get_current_user`, `CurrentUserDep`

## Adding New Endpoints

1. Create/update model in `app/models/`
2. Create/update schema in `app/schemas/`
3. Add router in `app/routers/` or extend existing
4. Register router in `app/main.py`

## Linting & Type Checking

```bash
# Run any linting tools if configured
```

## Notes

- All monetary values stored as floats
- Timestamps use UTC
- Purchase module is independent from transaction/expense module
