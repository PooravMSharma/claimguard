"""Shared test setup.

Database tests run against a separate database (<name>_test) that is created
fresh, migrated to the latest schema, and dropped at the end. Your development
data is never touched. Each test runs inside a transaction that is rolled back.
"""

from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alembic_config(url: URL) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def test_db_url() -> Generator[URL, None, None]:
    base = make_url(get_settings().database_url)
    test_name = f"{base.database}_test"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{test_name}"'))
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable: {exc.orig}")

    url = base.set(database=test_name)
    command.upgrade(_alembic_config(url), "head")
    yield url

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def alembic_cfg(test_db_url: URL) -> Config:
    return _alembic_config(test_db_url)


@pytest.fixture(scope="session")
def test_engine(test_db_url: URL) -> Generator[Engine, None, None]:
    engine = create_engine(test_db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(test_engine: Engine) -> Generator[Session, None, None]:
    """A session whose changes are rolled back after each test."""
    conn = test_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()