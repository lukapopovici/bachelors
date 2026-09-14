from pathlib import Path

from alembic import command
from alembic.config import Config

from src.config import validate_security_settings
from src.database import DATABASE_URL


def main() -> None:
    validate_security_settings()
    migration_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    migration_config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    command.upgrade(migration_config, "head")


if __name__ == "__main__":
    main()