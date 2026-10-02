"""Explicit stopped-service restore with a mandatory pre-restore backup."""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess

from evolving_companion.backup import backup_database
from evolving_companion.deployment import (
    DeploymentPaths,
    check_sqlite,
    load_server_env,
    database_character_ids,
)


def service_is_stopped() -> bool:
    result = subprocess.run(
        ["systemctl", "show", "si.service", "--property=ActiveState", "--value"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.strip() in {"inactive", "failed"}


def restore_database(
    backup: Path, database: Path, backup_dir: Path, *, service_stopped: bool
) -> Path:
    if not service_stopped:
        raise ValueError("Stop si.service before restoring")
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
    parser.add_argument("--env-file", type=Path, default=Path("/opt/si/config/si.env"))
    args = parser.parse_args()
    try:
        load_server_env(args.env_file)
        paths = DeploymentPaths.from_environment(args.env_file)
        pre_restore = restore_database(
            args.backup,
            paths.database,
            paths.backups,
            service_stopped=service_is_stopped(),
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
