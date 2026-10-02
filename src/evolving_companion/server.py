"""Deployment entry selecting the existing transport, not a transport framework."""

import os
import sys
from pathlib import Path

from evolving_companion.deployment import (
    DeploymentPaths,
    check_deployment,
    load_server_env,
    print_checks,
)


def main() -> int:
    if sys.platform != "linux" or os.geteuid() == 0:
        print("Server Core requires a non-root Linux user (si).")
        return 78
    env_file = Path("/opt/si/config/si.env")
    try:
        load_server_env(env_file)
        valid = (
            print_checks(check_deployment(DeploymentPaths.from_environment(env_file)))
            == 0
        )
    except Exception as error:
        print(f"Server configuration FAILED ({type(error).__name__})")
        return 78
    if not valid:
        return 78
    from evolving_companion.qq_cli import main as run_qq

    return run_qq(load_environment=False)


if __name__ == "__main__":
    raise SystemExit(main())
