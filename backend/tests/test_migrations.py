"""
Runs the real Alembic migration chain against a throwaway SQLite file and
checks the resulting schema matches app.database.Base.metadata. This is what
catches "I changed a model but forgot to generate/update a migration" — a model
change with no matching migration would otherwise pass every other test, since
those all run against Base.metadata.create_all() rather than the migrations
themselves.

The checks used to compare column *names* and nothing else. That left the
things most likely to be lost silently: three revisions in this chain use
batch_alter_table, which on SQLite physically drops and recreates the table,
and a unique constraint or foreign key dropped in that rebuild would not have
shown up here. Nor did anything ever execute a downgrade(), so all five
downgrade bodies — including the hand-written Postgres enum rebuilds and the
two that restore data — were unrun code.
"""
import pathlib
import tempfile
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.database import Base

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


@contextmanager
def _migrated_database():
    """Yields an inspector over a fresh database at head."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = pathlib.Path(tmp_dir) / "migration_check.db"
        db_url = f"sqlite:///{db_path}"

        from app.config import settings

        original_url = settings.database_url
        # migrations/env.py reads this at run time.
        settings.database_url = db_url
        try:
            yield db_url, _alembic_config()
        finally:
            settings.database_url = original_url


@pytest.fixture(scope="module")
def migrated():
    with _migrated_database() as (db_url, config):
        command.upgrade(config, "head")
        engine = create_engine(db_url)
        try:
            yield inspect(engine)
        finally:
            engine.dispose()


def test_every_model_table_exists(migrated):
    migrated_tables = set(migrated.get_table_names()) - {"alembic_version"}
    model_tables = set(Base.metadata.tables.keys())
    assert migrated_tables == model_tables


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_columns_match_the_model(migrated, table_name):
    """Names, types and nullability — not just names."""
    table = Base.metadata.tables[table_name]
    migrated_columns = {col["name"]: col for col in migrated.get_columns(table_name)}

    assert set(migrated_columns) == {col.name for col in table.columns}

    for column in table.columns:
        actual = migrated_columns[column.name]
        assert actual["nullable"] == column.nullable, (
            f"{table_name}.{column.name} nullability differs: migration says "
            f"nullable={actual['nullable']}, model says {column.nullable}"
        )
        # Compare the rendered SQL type. Numeric(18, 6) silently becoming
        # Numeric(12, 2) is exactly the drift a name-only check missed, and
        # it is the kind that loses money rather than raising.
        assert str(actual["type"]).upper() == str(column.type).upper(), (
            f"{table_name}.{column.name} type differs: migration has "
            f"{actual['type']}, model has {column.type}"
        )


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_unique_constraints_survive_the_batch_rebuilds(migrated, table_name):
    """
    Every uniqueness rule the model declares must exist in the migrated
    schema, whether the model spells it as a UniqueConstraint, a unique
    Index, or `unique=True` on the column.

    SQLite reports these inconsistently — some as constraints, some as
    indexes — so the assertion is on the *column sets*, not on the names.
    """
    table = Base.metadata.tables[table_name]

    expected = {
        tuple(sorted(c.name for c in constraint.columns))
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    expected |= {
        tuple(sorted(c.name for c in index.columns))
        for index in table.indexes
        if index.unique
    }
    expected |= {
        (column.name,) for column in table.columns if column.unique
    }

    actual = {
        tuple(sorted(uc["column_names"]))
        for uc in migrated.get_unique_constraints(table_name)
    }
    actual |= {
        tuple(sorted(index["column_names"]))
        for index in migrated.get_indexes(table_name)
        if index.get("unique")
    }

    missing = expected - actual
    assert not missing, f"{table_name} is missing unique constraint(s) on {missing}"


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_foreign_keys_survive_the_batch_rebuilds(migrated, table_name):
    """
    A foreign key silently dropped during a SQLite table rebuild is the
    failure this chain is most exposed to, and nothing checked for it.
    """
    table = Base.metadata.tables[table_name]

    expected = {
        (fk.parent.name, fk.column.table.name)
        for fk in table.foreign_keys
    }
    actual = {
        (column, fk["referred_table"])
        for fk in migrated.get_foreign_keys(table_name)
        for column in fk["constrained_columns"]
    }

    missing = expected - actual
    assert not missing, f"{table_name} is missing foreign key(s) {missing}"


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_declared_indexes_exist(migrated, table_name):
    """
    The hot-path indexes are only worth adding if they are actually created.
    """
    table = Base.metadata.tables[table_name]
    expected = {
        tuple(column.name for column in index.columns) for index in table.indexes
    }
    actual = {
        tuple(index["column_names"]) for index in migrated.get_indexes(table_name)
    }
    missing = expected - actual
    assert not missing, f"{table_name} is missing index(es) on {missing}"


def test_the_chain_downgrades_and_upgrades_again():
    """
    Runs every downgrade() in the chain, then every upgrade() again.

    None of them had ever been executed. Two restore data, three drop
    Postgres enum types by hand, and one rebuilds a table — the parts of a
    migration most likely to be wrong were the parts with no coverage at
    all. A round trip also proves the revisions are reversible in the order
    they claim to be.
    """
    with _migrated_database() as (db_url, config):
        command.upgrade(config, "head")
        command.downgrade(config, "base")

        engine = create_engine(db_url)
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
        engine.dispose()
        assert remaining == set(), f"downgrade left {remaining} behind"

        command.upgrade(config, "head")

        engine = create_engine(db_url)
        restored = set(inspect(engine).get_table_names()) - {"alembic_version"}
        engine.dispose()
        assert restored == set(Base.metadata.tables.keys())
