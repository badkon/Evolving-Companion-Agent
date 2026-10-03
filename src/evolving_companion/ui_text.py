"""Chinese presentation labels; internal keys/status values remain unchanged."""

PAGE_LABELS = {
    "Start": "启动",
    "Stop": "停止",
    "Restart": "重启",
    "Status": "状态",
    "Logs": "日志",
    "Configure": "配置",
    "Exit": "退出",
}

FIELD_LABELS = {
    "character": "角色",
    "internal_identity": "内部 ID",
    "runtime": "运行状态",
    "configuration": "配置",
    "runtime_db": "运行数据库",
    "transport": "聊天连接",
    "health": "本地检查",
    "LLM": "语言模型（LLM）",
    "Embedding": "嵌入模型",
    "Reranker": "重排模型",
    "Transport": "聊天连接",
    "Runtime DB": "运行数据库",
}

STATUS_LABELS = {
    "Running": "运行中",
    "Stopped": "已停止",
    "Failed": "运行失败",
    "Unknown": "未知",
    "OK": "正常",
    "Error": "错误",
    "Incomplete": "未完成",
    "Configured": "已配置",
    "Missing": "未配置",
    "Available": "可用",
    "Unavailable": "不可用",
    "none": "无聊天连接（none）",
    "qq": "QQ（SnowLuma / OneBot）",
    "qq — SnowLuma / OneBot": "QQ（SnowLuma / OneBot）",
    "OK (local only)": "通过（仅本地检查）",
    "Error (local only)": "失败（仅本地检查）",
}


def display_value(value: str) -> str:
    return STATUS_LABELS.get(value, value)


def check_text(text: str) -> str:
    """Present known local-check output in Chinese, without changing check results."""
    names = {
        "Character Seed / Identity": "角色初始数据 / 身份",
        "World Seed": "世界初始数据",
        "Runtime Directory / Writable SQLite Probe": "运行目录 / SQLite 写入检查",
        "Runtime DB (read-only)": "运行数据库（只读）",
        "SQLite": "SQLite",
        "LLM API Key": "语言模型 API 密钥",
        "SiliconFlow API Key": "SiliconFlow API 密钥",
        "Embedding / Reranker Provider": "嵌入 / 重排服务",
        "Transport": "聊天连接",
        "App Version": "应用版本",
        "Application configuration": "应用配置",
        "Health configuration": "本地检查配置",
    }
    lines = []
    for line in text.splitlines():
        for name, label in names.items():
            if line.startswith(name):
                line = label + "：" + line[len(name) :].strip()
                break
        line = line.replace(
            "NOT INITIALIZED (offline; no DB created)",
            "尚未初始化（离线检查；未创建数据库）",
        )
        line = line.replace("DEFERRED (offline)", "暂缓（离线检查）")
        line = line.replace("CONFIGURED", "已配置").replace("MISSING", "未配置")
        line = line.replace("FAILED (", "失败（")
        if "失败（" in line:
            line = line.replace(")", "）")
        if line.endswith("OK"):
            line = line[:-2] + "通过"
        lines.append(line)
    return "\n".join(lines)
