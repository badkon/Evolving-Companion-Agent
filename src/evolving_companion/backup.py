"""Reusable SQLite online backup; never copy a live database file."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from evolving_companion.runtime_config import (
    RuntimePaths,
    check_sqlite,
    load_runtime_env,
)


def backup_database(
    database: Path, backup_dir: Path, *, prefix: str = "si_001"
) -> Path:
    database = database.resolve()
    check_sqlite(database)
    if prefix not in {"si_001", "pre_restore"}:
        raise ValueError("Invalid backup prefix")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = backup_dir / f"{prefix}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.db"
    # Exclusive creation prevents timestamp collisions from overwriting any backup.
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        ) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
        check_sqlite(target)
    except Exception:
        # Only our newly reserved incomplete output is removed, never old backups.
        target.unlink(missing_ok=True)
        raise
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("config/si.env"))
    args = parser.parse_args()
    try:
        load_runtime_env(args.env_file)
        paths = RuntimePaths.from_environment(args.env_file)
        print(f"Backup created: {backup_database(paths.database, paths.backups)}")
    except Exception as error:
        print(
            f"Backup failed ({type(error).__name__}); no existing backup overwritten."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
