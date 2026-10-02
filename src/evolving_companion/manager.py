"""si console entry; only deployed Linux instances, never developer .env.local."""

import argparse
from pathlib import Path
import sys

from evolving_companion.deployment import DeploymentPaths, load_server_env


def main() -> int:
    parser = argparse.ArgumentParser(description="SI Manager — 本地 Linux 运维 TUI")
    parser.add_argument(
        "command", nargs="?", choices=["setup"], help="首次配置 / 安全重新配置"
    )
    parser.add_argument("--env-file", type=Path, default=Path("/opt/si/config/si.env"))
    args = parser.parse_args()
    if sys.platform != "linux":
        print("SI Manager is intended for Linux deployment.")
        return 0
    try:
        from evolving_companion.manager_app import SIManagerApp
        from evolving_companion.manager_services import (
            DeploymentFacade,
            SystemdServiceManager,
        )
        from evolving_companion.setup_app import SetupApp
        from evolving_companion.setup_services import SetupService

        systemd = SystemdServiceManager()
        setup = SetupService(args.env_file, systemd)
        if args.command == "setup":
            SetupApp(setup).run()
            return 0
        load_server_env(args.env_file)

        facade = DeploymentFacade(
            DeploymentPaths.from_environment(args.env_file), systemd
        )
        SIManagerApp(facade, setup_service=setup).run()
    except Exception as error:
        print(
            f"SI Manager failed ({type(error).__name__}); check local deployment/terminal permissions."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
