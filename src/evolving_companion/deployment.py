"""Offline deployment checks; no Character initialization or network calls."""

from contextlib import closing, ExitStack
from dataclasses import dataclass
from importlib.metadata import version
import os
from pathlib import Path
import sqlite3
import stat
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from dotenv import load_dotenv

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.memory_provider_config import create_memory_providers
from evolving_companion.qq_adapter import _identifier
from evolving_companion.world import load_world_seed


@dataclass(frozen=True)
class DeploymentPaths:
    root: Path
    app: Path
    env_file: Path
    database: Path
    backups: Path

    @classmethod
    def from_environment(cls, env_file: Path | None = None) -> "DeploymentPaths":
        root = Path(os.environ.get("SI_DEPLOY_ROOT", "/opt/si")).resolve()
        return cls(
            root,
            root / "app",
            env_file or root / "config/si.env",
            Path(
                os.environ.get("SI_RUNTIME_DB", str(root / "runtime/si_001.db"))
            ).resolve(),
            root / "backups",
        )


def load_server_env(path: Path) -> None:
    """Entry-only loading; systemd env files do NOT expand ${variables}."""
    load_dotenv(path, override=False, interpolate=False)
    # Deployment template uses one SiliconFlow key; generic adapter remains generic.
    key = os.environ.get("SILICONFLOW_API_KEY", "")
    for name in ("SI_MEMORY_EMBEDDING_API_KEY", "SI_MEMORY_RERANKER_API_KEY"):
        if name not in os.environ:
            os.environ[name] = key


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    status: str


def check_sqlite(path: Path) -> None:
    """Read-only integrity check; missing files are never implicitly created."""
    with path.open("rb") as file:
        if file.read(16) != b"SQLite format 3\x00":
            raise ValueError("Not a SQLite database")
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")


def check_env_permissions(path: Path) -> None:
    if os.name == "posix":
        info = path.stat()
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise PermissionError("Environment file must be 0600")
        import pwd

        if info.st_uid != pwd.getpwnam("si").pw_uid:
            raise PermissionError("Environment file must be owned by si")


def database_character_ids(path: Path) -> set[str]:
    """Inspect existing Character-owned keys without migrating or initializing."""
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        return {
            row[0]
            for table in (
                "character_state",
                "character_runtime",
                "character_life_context",
            )
            if table in tables
            for row in connection.execute(f"SELECT character_id FROM {table}")
        }


def check_deployment(
    paths: DeploymentPaths, *, health: bool = False, offline: bool = False
) -> tuple[CheckResult, ...]:
    results: list[CheckResult] = []

    def check(name: str, operation) -> None:
        try:
            operation()
        except Exception as error:
            # Pydantic errors, filesystem errors and URLs can contain private input.
            results.append(CheckResult(name, False, f"FAILED ({type(error).__name__})"))
        else:
            results.append(CheckResult(name, True, "OK"))

    def required_files() -> None:
        for path in (
            paths.app / "pyproject.toml",
            paths.app / "uv.lock",
            paths.app / "deploy/systemd/si.service",
            paths.env_file,
        ):
            if not path.is_file():
                raise FileNotFoundError
        check_env_permissions(paths.env_file)

    def runtime_directory() -> None:
        configured = os.environ.get("SI_RUNTIME_DB", "")
        if (
            not configured
            or not Path(configured).is_absolute()
            or Path(configured).resolve() != paths.database
        ):
            raise ValueError("Server requires explicit absolute SI_RUNTIME_DB")
        if not paths.database.is_absolute() or paths.database.is_relative_to(
            paths.app.resolve()
        ):
            raise ValueError("Runtime DB must be outside app")
        if not paths.database.parent.is_dir():
            raise FileNotFoundError
        # Probe actual write permission and SQLite creation without touching runtime DB.
        with TemporaryDirectory(
            prefix=".si-check-", dir=paths.database.parent
        ) as directory:
            with closing(sqlite3.connect(Path(directory) / "probe.db")) as connection:
                connection.execute("CREATE TABLE probe(id INTEGER)")
                connection.commit()

    def sqlite_check() -> None:
        if paths.database.exists():
            check_sqlite(paths.database)
        elif health:
            raise FileNotFoundError
        else:
            runtime_directory()

    def identity_binding() -> None:
        seed = load_character_seed_data(paths.app / "data/characters/si_001.yaml")
        if not paths.database.exists():
            return
        if database_character_ids(paths.database) - {str(seed.identity.internal_id)}:
            raise ValueError(
                "Character identity mismatch; do not initialize a new Character"
            )

    def providers() -> None:
        if (
            os.environ.get("SI_MEMORY_EMBEDDING_PROVIDER") != "api"
            or os.environ.get("SI_MEMORY_RERANKER_PROVIDER") != "api"
        ):
            raise ValueError("Server profile requires api/api")
        if offline:
            for name in (
                "SI_MEMORY_EMBEDDING_API_ID",
                "SI_MEMORY_EMBEDDING_MODEL",
                "SI_MEMORY_RERANKER_MODEL",
            ):
                if not os.environ.get(name, "").strip():
                    raise ValueError("Memory provider configuration is incomplete")
            if int(os.environ.get("SI_MEMORY_EMBEDDING_DIMENSION", "0")) <= 0:
                raise ValueError("Invalid embedding dimension")
            if float(os.environ.get("SI_MEMORY_API_TIMEOUT", "30")) <= 0:
                raise ValueError("Invalid API timeout")
            for name in ("SI_MEMORY_EMBEDDING_API_URL", "SI_MEMORY_RERANKER_API_URL"):
                endpoint = urlsplit(os.environ.get(name, ""))
                if endpoint.scheme != "https" or not endpoint.hostname:
                    raise ValueError("Invalid API endpoint")
                _ = endpoint.port
            return
        with ExitStack() as resources:
            create_memory_providers(
                resources
            )  # Validate only: lazy clients, no requests.

    def transport() -> None:
        if offline and os.environ.get("SI_CHAT_TRANSPORT") == "none":
            return
        if os.environ.get("SI_CHAT_TRANSPORT") != "qq":
            raise ValueError("Only the existing qq entry is supported")
        _identifier(os.environ.get("SI_QQ_BOT_USER_ID", ""), user=True)
        allowed = [
            item.strip()
            for item in os.environ.get("SI_QQ_ALLOWED_USER_IDS", "").split(",")
            if item.strip()
        ]
        if not allowed:
            raise ValueError("An explicit allowlist is required")
        for item in allowed:
            _identifier(item, user=True)
        url = urlsplit(os.environ.get("SI_ONEBOT_WS_URL", ""))
        if (
            url.scheme not in {"ws", "wss"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid transport endpoint")
        _ = url.port  # Validate numeric port without making a connection.

    check("Required Files / Env Permissions", required_files)
    check("Character Seed / Identity", identity_binding)
    check(
        "World Seed", lambda: load_world_seed(paths.app / "data/worlds/si_world.yaml")
    )
    check("Runtime Directory / Writable SQLite Probe", runtime_directory)
    if offline and health and not paths.database.exists():
        results.append(
            CheckResult(
                "Runtime DB (read-only)",
                True,
                "NOT INITIALIZED (offline; no DB created)",
            )
        )
    else:
        check("SQLite" if not health else "Runtime DB (read-only)", sqlite_check)
    for name, env_name in (
        ("LLM API Key", "DEEPSEEK_API_KEY"),
        ("SiliconFlow API Key", "SILICONFLOW_API_KEY"),
    ):
        configured = bool(os.environ.get(env_name, "").strip())
        results.append(
            CheckResult(
                name,
                configured or offline,
                "CONFIGURED"
                if configured
                else ("DEFERRED (offline)" if offline else "MISSING"),
            )
        )
    check("Embedding / Reranker Provider", providers)
    check("Transport", transport)
    check("App Version", lambda: version("evolving-companion-agent"))
    return tuple(results)


def print_checks(results: tuple[CheckResult, ...]) -> int:
    for result in results:
        print(f"{result.name:<42} {result.status}")
    return 0 if all(result.ok for result in results) else 1
