"""API-only application entry selecting QQ or a transport-free Core lifetime."""

import argparse
import os
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
    args = parser.parse_args()
    try:
        load_runtime_env(args.env_file)
        if (
            print_checks(check_runtime(RuntimePaths.from_environment(args.env_file)))
            != 0
        ):
            return 78
    except Exception as error:
        print(f"Application configuration FAILED ({type(error).__name__})")
        return 78
    if os.environ.get("SI_CHAT_TRANSPORT") == "none":
        from evolving_companion.runtime import run_core_only

        return run_core_only()

    from evolving_companion.qq_cli import main as run_qq

    return run_qq(load_environment=False)


if __name__ == "__main__":
    raise SystemExit(main())
