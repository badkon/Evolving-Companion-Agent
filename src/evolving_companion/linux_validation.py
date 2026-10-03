"""Offline Linux acceptance support; temporary backup/restore never touches runtime."""

from contextlib import closing
from importlib.metadata import distributions
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
from tempfile import TemporaryDirectory

from evolving_companion.backup import backup_database
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.deployment import (
    CheckResult,
    DeploymentPaths,
    check_deployment,
    load_server_env,
    print_checks,
)
from evolving_companion.restore import restore_database


def check_owned_path(
    path: Path, uid: int, gid: int, mode: int, *, directory: bool
) -> None:
    """Check exact private deployment permissions without repairing them."""
    if path.is_symlink():
        raise ValueError("Symlinked deployment path")
    info = path.stat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode):
        raise ValueError("Unexpected deployment path type")
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, mode):
        raise PermissionError("Deployment owner/mode mismatch")


def check_backup_restore(parent: Path, character_id: str) -> None:
    """Exercise the existing backup/restore helpers on an isolated synthetic DB."""
    with TemporaryDirectory(prefix=".si-validation-", dir=parent) as directory:
        root = Path(directory)
        database = root / "probe.db"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE character_runtime(character_id TEXT)")
            connection.execute(
                "INSERT INTO character_runtime VALUES (?)", (character_id,)
            )
            connection.execute("CREATE TABLE probe(value TEXT)")
            connection.execute("INSERT INTO probe VALUES ('before')")
        snapshot = backup_database(database, root / "backups")
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE probe SET value='after'")
        previous = restore_database(
            snapshot, database, root / "backups", service_stopped=True
        )
        for path, expected in ((database, "before"), (previous, "after")):
            with closing(sqlite3.connect(path)) as connection:
                if connection.execute("SELECT value FROM probe").fetchone() != (
                    expected,
                ):
                    raise ValueError("Backup/restore did not preserve data")
                if connection.execute(
                    "SELECT character_id FROM character_runtime"
                ).fetchone() != (character_id,):
                    raise ValueError("Backup/restore identity mismatch")


def check_linux_installation(paths: DeploymentPaths) -> tuple[CheckResult, ...]:
    results: list[CheckResult] = []

    def check(name, operation) -> None:
        try:
            operation()
        except Exception as error:
            results.append(CheckResult(name, False, f"FAILED ({type(error).__name__})"))
        else:
            results.append(CheckResult(name, True, "OK"))

    def permissions() -> None:
        import grp
        import pwd

        owner = pwd.getpwnam("si")
        group = grp.getgrnam("si")
        if owner.pw_uid == 0 or owner.pw_gid != group.gr_gid:
            raise ValueError("Invalid si account")
        for name, mode in (
            ("", 0o750),
            ("app", 0o750),
            ("config", 0o700),
            ("runtime", 0o700),
            ("logs", 0o700),
            ("backups", 0o700),
            ("scripts", 0o750),
            ("data", 0o750),
        ):
            check_owned_path(
                paths.root / name, owner.pw_uid, group.gr_gid, mode, directory=True
            )
        check_owned_path(
            paths.env_file, owner.pw_uid, group.gr_gid, 0o600, directory=False
        )
        for path, mode in (
            (Path("/usr/local/bin/si"), 0o755),
            (Path("/etc/systemd/system/si.service"), 0o644),
        ):
            check_owned_path(path, 0, 0, mode, directory=False)
        if (
            Path("/etc/systemd/system/si.service").read_bytes()
            != (paths.app / "deploy/systemd/si.service").read_bytes()
        ):
            raise ValueError("Installed unit differs from reviewed application unit")

    def environment() -> None:
        if (
            sys.version_info < (3, 12)
            or Path(sys.prefix).resolve() != (paths.app / ".venv").resolve()
        ):
            raise ValueError("Incorrect Python environment")
        if shutil.which("uv") is None or shutil.which("si") != "/usr/local/bin/si":
            raise ValueError("uv/si must be on the service user's PATH")
        for name in ("si", "python"):
            if not os.access(paths.app / ".venv/bin" / name, os.X_OK):
                raise PermissionError("Venv entry point unavailable")
        installed = {item.metadata["Name"].lower() for item in distributions()}
        if installed & {"torch", "transformers", "sentence-transformers"}:
            raise ValueError("API-only environment must not install local ML packages")
        # Import entry modules; never run apps or initialize providers/Character.
        from evolving_companion import manager_app, setup_app, server

        _ = (manager_app, setup_app, server)

    check("Installation Ownership / Permissions / Unit", permissions)
    check("Python / uv / si / API-only Imports", environment)
    results.extend(check_deployment(paths, health=True, offline=True))
    if all(item.ok for item in results):
        seed = load_character_seed_data(paths.app / "data/characters/si_001.yaml")
        check(
            "Temporary SQLite Backup / Restore / UUID",
            lambda: check_backup_restore(paths.backups, str(seed.identity.internal_id)),
        )
    return tuple(results)


def main() -> int:
    if sys.platform != "linux":
        print("Requires Linux; no systemd validation performed.")
        return 1
    if os.geteuid() == 0:
        print(
            "Run validation as si via deploy/validate_linux.sh; root cannot prove si write access."
        )
        return 1
    try:
        paths = DeploymentPaths.from_environment()
        load_server_env(paths.env_file)
        paths = DeploymentPaths.from_environment(paths.env_file)
        return print_checks(check_linux_installation(paths))
    except Exception as error:
        print(f"Offline Linux validation FAILED ({type(error).__name__})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
