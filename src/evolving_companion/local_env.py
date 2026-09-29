"""Load optional, untracked project-local environment settings at app entry points."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENV_PATH = PROJECT_ROOT / ".env.local"


def load_local_env(env_path: Path | None = None) -> bool:
    """Load a local env file without overriding the process environment.

    Returns whether a non-empty DeepSeek API key is configured after loading.
    An explicit path is accepted so tests can use isolated temporary files.
    """
    load_dotenv(dotenv_path=env_path or LOCAL_ENV_PATH, override=False)
    return bool(os.environ.get("DEEPSEEK_API_KEY"))
