"""Offline application configuration check."""

import argparse
from pathlib import Path

from evolving_companion.runtime_config import (
    RuntimePaths,
    check_runtime,
    load_runtime_env,
    print_checks,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("config/si.env"))
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Check configuration without API keys; does not validate network availability",
    )
    parser.add_argument(
        "--project-root", type=Path, help="Root containing the existing seed data"
    )
    args = parser.parse_args()
    try:
        load_runtime_env(args.env_file)
        return print_checks(
            check_runtime(
                RuntimePaths.from_environment(args.env_file, args.project_root),
                offline=args.offline,
            )
        )
    except Exception as error:
        print(f"Application configuration FAILED ({type(error).__name__})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
