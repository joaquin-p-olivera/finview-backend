from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.cors import CORSMiddleware as StarletteCORSMiddleware

from .config import get_settings
from .idempotency import IdempotencyMiddleware
from .routers import auth, categories, statements, stats, transactions, purchase

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Create a .env file with DATABASE_URL=mysql+pymysql://user:pass@host:3306/dbname")
    if not settings.SECRET_KEY:
        raise RuntimeError("SECRET_KEY is not set. Create a .env file with a secure secret key.")
    yield


app = FastAPI(title="Finview - Analizador de gastos", version="1.3.0", lifespan=lifespan)

# Added before CORS so it runs inside it: replayed responses still get CORS headers.
app.add_middleware(IdempotencyMiddleware)

app.add_middleware(
    StarletteCORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Accepts HEAD too so uptime monitors (which often send HEAD) can ping it to
# keep the Render free instance awake. It doesn't touch the database.
@app.api_route("/health", methods=["GET", "HEAD"])
def health_check():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(statements.router)
app.include_router(stats.router)
app.include_router(transactions.router)
app.include_router(purchase.router)

