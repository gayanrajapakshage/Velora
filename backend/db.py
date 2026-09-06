import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv(Path(__file__).with_name(".env"), override=True)


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it to backend/.env")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


_engine: Engine | None = None
_SessionFactory = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        # One pool for the process. A new Engine per request would open
        # many Neon connections under the concurrent simulated patrons.
        _engine = create_engine(database_url(), pool_pre_ping=True)
    return _engine


def get_session() -> Session:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory()
