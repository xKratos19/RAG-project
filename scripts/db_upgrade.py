from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import settings


def main() -> None:
    config = Config("alembic.ini")
    needs_legacy_stamp = False
    engine = create_engine(settings.database_url, future=True)
    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "rag_jobs" in table_names:
            if "alembic_version" not in table_names:
                needs_legacy_stamp = True
            else:
                version_rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
                if not version_rows:
                    needs_legacy_stamp = True
    if needs_legacy_stamp:
        command.stamp(config, "0001_initial_schema")
    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))
    command.upgrade(config, "head")
    print("Database upgraded to head.")


if __name__ == "__main__":
    main()
