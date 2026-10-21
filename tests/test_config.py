from __future__ import annotations

import pytest

from saas_billing.config import Settings


@pytest.mark.parametrize(
    "given",
    [
        "postgres://billing:secret@db.render.internal:5432/billing",
        "postgresql://billing:secret@db.render.internal:5432/billing",
    ],
)
def test_managed_postgres_urls_are_rewritten_to_psycopg3(given: str) -> None:
    settings = Settings(_env_file=None, database_url=given)
    assert (
        settings.database_url
        == "postgresql+psycopg://billing:secret@db.render.internal:5432/billing"
    )


@pytest.mark.parametrize(
    "given",
    ["sqlite://", "sqlite:///./billing.db", "postgresql+psycopg://billing:billing@db:5432/billing"],
)
def test_explicit_driver_urls_are_left_alone(given: str) -> None:
    assert Settings(_env_file=None, database_url=given).database_url == given
