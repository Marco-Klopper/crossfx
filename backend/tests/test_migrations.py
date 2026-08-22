"""
Runs the real Alembic migration chain against a throwaway SQLite file and
checks the resulting schema matches app.database.Base.metadata table-for-table
and column-for-column. This is what catches "I changed a model but forgot to
generate/update a migration" — a model change with no matching migration
would otherwise pass every other test, since those all run against
Base.metadata.create_all() rather than the migrations themselves.
"""
import pathlib
import tempfile

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.database import Base

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent


def test_alembic_upgrade_head_matches_models():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = pathlib.Path(tmp_dir) / "migration_check.db"
        db_url = f"sqlite:///{db_path}"

        from app.config import settings

        original_url = settings.database_url
        settings.database_url = db_url  # migrations/env.py reads this at run time
        try:
            alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
            alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
            command.upgrade(alembic_cfg, "head")
        finally:
            settings.database_url = original_url

        engine = create_engine(db_url)
        inspector = inspect(engine)

        migrated_tables = set(inspector.get_table_names()) - {"alembic_version"}
        model_tables = set(Base.metadata.tables.keys())
        assert migrated_tables == model_tables

        for table_name, table in Base.metadata.tables.items():
            migrated_columns = {col["name"] for col in inspector.get_columns(table_name)}
            model_columns = {col.name for col in table.columns}
            assert migrated_columns == model_columns, f"column mismatch in {table_name}"

        engine.dispose()
