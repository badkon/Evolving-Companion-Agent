"""Operator-only configuration boundary; no Core, DB or process initialization."""

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from threading import RLock
from urllib.parse import urlsplit

import yaml

from evolving_companion.character_data import CharacterSeedData
from evolving_companion.config_env import (
    ApplicationEnvService,
    EDITABLE_FIELDS,
    SECRET_FIELDS,
    EnvEditError,
    OperationResult,
    redact,
)
from evolving_companion.qq_adapter import _identifier
from evolving_companion.ui_text import check_text

# Labels are presentation only. There is no second settings store.
FIELDS = {
    "SI_LLM_API_URL": "语言模型 API 地址（OpenAI-compatible）",
    "SI_LLM_MODEL": "语言模型名称",
    "SI_MEMORY_EMBEDDING_API_URL": "Embedding API 地址",
    "SI_MEMORY_EMBEDDING_API_ID": "Embedding 服务标识",
    "SI_MEMORY_EMBEDDING_MODEL": "Embedding 模型",
    "SI_MEMORY_EMBEDDING_DIMENSION": "Embedding 维度",
    "SI_MEMORY_RERANKER_API_URL": "Reranker API 地址",
    "SI_MEMORY_RERANKER_MODEL": "Reranker 模型",
    "SI_CHAT_TRANSPORT": "聊天连接",
    "SI_ONEBOT_WS_URL": "SnowLuma / OneBot 正向 WebSocket 地址",
    "SI_QQ_BOT_USER_ID": "机器人 QQ 号",
    "SI_QQ_ALLOWED_USER_IDS": "允许私聊的 QQ 号（逗号分隔）",
}
SECRET_LABELS = {
    "DEEPSEEK_API_KEY": "语言模型 API 密钥",
    "SILICONFLOW_API_KEY": "SiliconFlow API 密钥",
    "SI_ONEBOT_ACCESS_TOKEN": "OneBot access token（可选）",
}
CHARACTER_FIELDS = {
    "working_name": ("当前称呼（Working Name）", ("identity", "working_name")),
    "traits": ("基础人格（每行一项）", ("personality", "baseline_traits")),
    "strangers": (
        "面对陌生人的表达与关系基线",
        ("personality", "social_tendencies", "strangers"),
    ),
    "familiar_people": (
        "面对熟人的表达与关系基线",
        ("personality", "social_tendencies", "familiar_people"),
    ),
    "solitude": ("独处倾向", ("personality", "social_tendencies", "solitude")),
    "likes": ("兴趣与偏好（每行一项）", ("seed_preferences", "likes")),
    "dislikes": ("不喜欢的事（每行一项）", ("seed_preferences", "dislikes")),
    "current_role": (
        "初始生活背景：角色（不改已有 Runtime）",
        ("initial_life_context", "current_role"),
    ),
}


def revision(text: str) -> str:
    return sha256(text.encode()).hexdigest()


class WebSetupService:
    def __init__(self, env_file: Path, project: Path, environment: Mapping[str, str]):
        self.project = project
        self.environment = dict(environment)
        self.env = ApplicationEnvService(env_file, project / "config/si.env.example")
        seed = project / "data/characters/si_001.yaml"
        # Reuse protected backup + expected-original atomic replacement for YAML too.
        self.seed = ApplicationEnvService(seed, seed)
        self.lock = RLock()

    def effective(self, updates: Mapping[str, str] | None = None) -> dict[str, str]:
        values = (
            self.env.defaults()
            | self.env.read()
            | dict(updates or {})
            | self.environment
        )
        for key in ("SI_MEMORY_EMBEDDING_API_KEY", "SI_MEMORY_RERANKER_API_KEY"):
            if key not in values:
                values[key] = values.get("SILICONFLOW_API_KEY", "")
        return values

    def snapshot(self) -> dict:
        with self.lock:
            original = self.env._text()
            values = self.env.defaults() | self.env.read()
            effective = self.effective()
            raw = self.seed._text()
            # Defaults are for display only; raw YAML remains authoritative.
            data = CharacterSeedData.model_validate(yaml.safe_load(raw)).model_dump(
                mode="json"
            )
            character = {}
            for key, (_, path) in CHARACTER_FIELDS.items():
                value = data
                for part in path:
                    value = value[part]
                character[key] = "\n".join(value) if isinstance(value, list) else value
            return {
                "version": revision(original),
                "character_version": revision(raw),
                "values": {
                    key: redact(values.get(key, ""), effective) for key in FIELDS
                },
                "secrets": {key: bool(effective.get(key)) for key in SECRET_FIELDS},
                "overrides": sorted(set(self.environment) & EDITABLE_FIELDS),
                "character": character,
                "identity": data["identity"],
                "yaml": raw,
            }

    def updates(self, payload: dict) -> dict[str, str]:
        values = payload.get("values", {})
        secrets = payload.get("secrets", {})
        if not isinstance(values, dict) or set(values) - set(FIELDS):
            raise ValueError("Unsupported fields")
        if not isinstance(secrets, dict) or set(secrets) - set(SECRET_FIELDS):
            raise ValueError("Unsupported secrets")
        result = dict(values)
        for key, action in secrets.items():
            if not isinstance(action, dict):
                raise ValueError("Invalid secret action")
            mode = action.get("mode", "keep")
            if mode == "clear":
                result[key] = ""
            elif mode == "replace":
                value = action.get("value")
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or value in {"********", "[REDACTED]", "已配置"}
                ):
                    raise ValueError("A replacement secret is required")
                result[key] = value
            elif mode != "keep":
                raise ValueError("Invalid secret mode")
        if any(
            not isinstance(v, str) or any(not c.isprintable() for c in v)
            for v in result.values()
        ):
            raise ValueError("Single line values required")
        return result

    def validate(self, payload: dict) -> OperationResult:
        updates = self.updates(payload)
        values = self.effective(updates)
        # Same runtime check in a fresh process; never mutate Manager's os.environ.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "evolving_companion.runtime_check",
                "--env-file",
                str(self.env.path),
                "--project-root",
                str(self.project),
            ],
            cwd=self.project,
            env=values,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        return OperationResult(
            result.returncode == 0,
            check_text(redact(result.stdout, values))
            or "本地检查进程未完成；请检查 Python 环境与权限（未显示原始 stderr）。",
        )

    def save(self, payload: dict) -> OperationResult:
        with self.lock:
            original = self.env._text()
            if payload.get("version") != revision(original):
                raise EnvEditError("配置已被修改，请重新加载页面。")
            updates = self.updates(payload)
            candidate = self.env.defaults() | self.env.read() | updates
            for key in (
                "SI_LLM_MODEL",
                "SI_MEMORY_EMBEDDING_MODEL",
                "SI_MEMORY_RERANKER_MODEL",
            ):
                if not candidate.get(key, "").strip():
                    raise ValueError("Model name required")
            for key in ("SI_MEMORY_EMBEDDING_PROVIDER", "SI_MEMORY_RERANKER_PROVIDER"):
                if candidate.get(key) != "api":
                    raise ValueError("Web setup supports API only")
            if candidate.get("SI_CHAT_TRANSPORT") == "qq":
                if candidate.get("SI_QQ_BOT_USER_ID"):
                    _identifier(candidate["SI_QQ_BOT_USER_ID"], user=True)
                for item in candidate.get("SI_QQ_ALLOWED_USER_IDS", "").split(","):
                    if item.strip():
                        _identifier(item.strip(), user=True)
            # Allow deliberately incomplete secrets (clear), but not invalid syntax.
            # Start remains gated by the authoritative runtime check.
            for key in (
                "SI_LLM_API_URL",
                "SI_MEMORY_EMBEDDING_API_URL",
                "SI_MEMORY_RERANKER_API_URL",
                "SI_ONEBOT_WS_URL",
            ):
                if key in updates and updates[key]:
                    url = urlsplit(updates[key])
                    schemes = (
                        {"ws", "wss"}
                        if key == "SI_ONEBOT_WS_URL"
                        else {"http", "https"}
                    )
                    if (
                        url.scheme not in schemes
                        or not url.hostname
                        or url.username
                        or url.password
                        or url.query
                        or url.fragment
                    ):
                        raise ValueError("Invalid endpoint")
                    _ = url.port
            if "SI_CHAT_TRANSPORT" in updates and updates["SI_CHAT_TRANSPORT"] not in {
                "none",
                "qq",
            }:
                raise ValueError("Invalid transport")
            if (
                "SI_MEMORY_EMBEDDING_DIMENSION" in updates
                and int(updates["SI_MEMORY_EMBEDDING_DIMENSION"]) <= 0
            ):
                raise ValueError("Invalid dimension")
            self.env.path.parent.mkdir(parents=True, exist_ok=True)
            self.env.update(updates, expected=original)
            return OperationResult(
                True, "应用配置已保存。请执行检查配置；运行中的 Core 需重启才能应用。"
            )

    def save_character(self, payload: dict) -> OperationResult:
        with self.lock:
            original = self.seed._text()
            if payload.get("version") != revision(original):
                raise EnvEditError("角色配置已被修改，请重新加载页面。")
            old = yaml.safe_load(original)
            if "yaml" in payload:
                data = yaml.safe_load(payload["yaml"])

                def retained(before, after):
                    if isinstance(before, dict):
                        if not isinstance(after, dict) or set(before) - set(after):
                            raise ValueError("Existing fields cannot be removed")
                        for key in before:
                            retained(before[key], after[key])

                retained(old, data)
            else:
                data = deepcopy(old)
                fields = payload.get("character", {})
                if not isinstance(fields, dict) or set(fields) - set(CHARACTER_FIELDS):
                    raise ValueError("Unsupported character fields")
                for key, value in fields.items():
                    if not isinstance(value, str):
                        raise ValueError("Text required")
                    target = data
                    path = CHARACTER_FIELDS[key][1]
                    for part in path[:-1]:
                        target = target.setdefault(part, {})
                    target[path[-1]] = (
                        [s.strip() for s in value.splitlines() if s.strip()]
                        if key in {"traits", "likes", "dislikes"}
                        else value
                    )
            for key, value in old["identity"].items():
                if key != "working_name" and data["identity"].get(key) != value:
                    raise ValueError("Character identity is locked")
            CharacterSeedData.model_validate(data)
            if not data["identity"]["working_name"].strip():
                raise ValueError("Working name cannot be empty")
            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            self.seed.backup(original)
            self.seed.atomic_write(text, expected=original)
            return OperationResult(
                True,
                "角色 seed 已保存；身份未改变。重启后应用，已有经历、记忆与生活状态不重写。",
            )
