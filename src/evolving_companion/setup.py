"""Cross-platform application setup entry; no service management."""

import argparse
from collections.abc import Sequence
from pathlib import Path
from evolving_companion.setup_services import SetupService


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SI 首次配置 — 应用配置")
    parser.add_argument("command", nargs="?", choices=["setup"])
    parser.add_argument("--env-file", type=Path, default=Path("config/si.env"))
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args(argv)
    try:
        from evolving_companion.setup_app import SetupApp

        SetupApp(SetupService(args.env_file, project_root=args.project_root)).run()
    except Exception as error:
        print(f"应用配置失败（{type(error).__name__}）；请检查配置和文件权限。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
