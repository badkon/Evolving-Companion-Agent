"""si opens Manager; si setup retains the existing configuration wizard."""

import argparse
from pathlib import Path
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SI 管理器 — 原生运行控制 / 应用配置")
    parser.add_argument("command", nargs="?", choices=["setup"])
    parser.add_argument("--env-file", type=Path, default=Path("config/si.env"))
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args(argv)
    if args.command == "setup":
        from evolving_companion.setup import main as setup

        arguments = ["--env-file", str(args.env_file)]
        if args.project_root is not None:
            arguments.extend(["--project-root", str(args.project_root)])
        return setup(arguments)
    try:
        from evolving_companion.manager_app import SIManagerApp
        from evolving_companion.manager_services import ManagerService

        SIManagerApp(
            ManagerService(args.env_file, project_root=args.project_root)
        ).run()
    except Exception as error:
        print(f"管理器启动失败（{type(error).__name__}）；请检查本地配置和终端。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
