"""Offline environment health, not a network/transport availability probe."""

import argparse
from pathlib import Path

from evolving_companion.deployment import (
    DeploymentPaths,
    check_deployment,
    load_server_env,
    print_checks,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("/opt/si/config/si.env"))
    args = parser.parse_args()
    try:
        load_server_env(args.env_file)
        return print_checks(
            check_deployment(
                DeploymentPaths.from_environment(args.env_file), health=True
            )
        )
    except Exception as error:
        print(f"Health configuration FAILED ({type(error).__name__})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
