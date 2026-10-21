from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from saas_billing.models import Base

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_head_matches_models(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from saas_billing.config import get_settings

    get_settings.cache_clear()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    get_settings.cache_clear()

    migrated = inspect(create_engine(url))
    expected_tables = set(Base.metadata.tables)
    assert expected_tables <= set(migrated.get_table_names())
    for table in expected_tables:
        model_cols = {c.name for c in Base.metadata.tables[table].columns}
        db_cols = {c["name"] for c in migrated.get_columns(table)}
        assert model_cols == db_cols, table
