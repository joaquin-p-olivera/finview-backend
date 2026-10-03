from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


settings = get_settings()


class Base(DeclarativeBase):
    pass


def _with_driver(database_url: str) -> str:
    # A bare postgresql:// URL (what Render hands out) picks SQLAlchemy's
    # default driver, which is psycopg2 up to 2.0 but psycopg 3 from 2.1.
    # Pin psycopg2, the driver in requirements.txt, so upgrades don't break.
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + database_url[len("postgresql://"):]
    return database_url


def _connect_args(database_url: str) -> dict:
    # Fail fast instead of hanging when the database can't be reached, and
    # send TCP keepalives so idle connections aren't silently dropped by the
    # network in between.
    if database_url.startswith("postgresql"):
        return {
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        }
    return {}


# pool_pre_ping checks each pooled connection before handing it out and
# transparently replaces it if the server closed it while idle, so the first
# request after a quiet period doesn't fail with a dead connection.
# pool_recycle retires connections before managed Postgres idle timeouts hit.
engine = create_engine(
    _with_driver(settings.DATABASE_URL),
    future=True,
    pool_pre_ping=True,
    pool_recycle=280,
    pool_timeout=15,
    connect_args=_connect_args(settings.DATABASE_URL),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
