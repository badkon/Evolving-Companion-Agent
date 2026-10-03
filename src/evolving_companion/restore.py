"""Explicit stopped-writer restore with a mandatory pre-restore backup."""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

from evolving_companion.backup import backup_database
from evolving_companion.runtime_config import (
    RuntimePaths,
    check_sqlite,
    load_runtime_env,
    database_character_ids,
)


def restore_database(
    backup: Path, database: Path, backup_dir: Path, *, writers_stopped: bool
) -> Path:
    if not writers_stopped:
        raise ValueError("Stop all database writers before restoring")
    backup, database = backup.resolve(), database.resolve()
    if backup == database:
        raise ValueError("Backup must differ from runtime database")
    check_sqlite(backup)
    check_sqlite(database)  # Refuse accidental restoration into an uninitialized path.
    if database_character_ids(database) != database_character_ids(backup):
        raise ValueError("Backup Character identity differs from current database")
    pre_restore = backup_database(database, backup_dir, prefix="pre_restore")
    with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(database)) as target:
            source.backup(target)
    check_sqlite(database)
    return pre_restore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path("config/si.env"))
    parser.add_argument(
        "--writers-stopped",
        action="store_true",
        help="Explicit confirmation that ALL database writers are stopped",
    )
    args = parser.parse_args()
    try:
        load_runtime_env(args.env_file)
        paths = RuntimePaths.from_environment(args.env_file)
        pre_restore = restore_database(
            args.backup,
            paths.database,
            paths.backups,
            writers_stopped=args.writers_stopped,
        )
        print(f"Restore completed; previous database preserved: {pre_restore}")
    except Exception as error:
        print(
            f"Restore refused/failed ({type(error).__name__}); inspect pre-restore backup if created."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
