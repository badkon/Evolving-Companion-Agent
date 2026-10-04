"""Console presentation and bounded, read-only inspection; no Core initialization."""

from contextlib import closing
from dataclasses import asdict
from importlib.metadata import version
import logging
import sqlite3

from evolving_companion.affective_console import (
    AffectiveConsoleService,
    empty_affective,
)
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.config_env import redact
from evolving_companion.manager_services import ManagerService
from evolving_companion.ui_text import display_value


class ConsoleService:
    def __init__(self, manager: ManagerService):
        self.manager = manager

    def clean(self, value):
        """Redact data before it reaches either HTML or JSON presentation."""
        values = self.manager.effective()

        def visit(item):
            if isinstance(item, str):
                return redact(item, values)
            if isinstance(item, dict):
                return {key: visit(val) for key, val in item.items()}
            if isinstance(item, list):
                return [visit(val) for val in item]
            return item

        return visit(value)

    def affective(self) -> dict:
        try:
            seed = load_character_seed_data(
                self.manager.project / "data/characters/si_001.yaml"
            )
            result = AffectiveConsoleService(
                self.manager.paths().database, seed.identity.internal_id
            ).read()
            return self.clean(result)
        except Exception as error:
            logging.getLogger(__name__).warning(
                "affective_console_read_failed: %s", type(error).__name__
            )
            return empty_affective("状态读取失败；请稍后刷新。", status="error")

    def memories(self, query: str = "", offset: int = 0) -> dict:
        database = self.manager.paths().database
        if not database.is_file():
            return {"items": [], "more": False, "message": "运行数据库尚未初始化。"}
        if len(query) > 200 or offset < 0 or offset > 1000000:
            raise ValueError("Invalid search range")
        # Never instantiate SQLiteStore here: its constructor initializes schema.
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only = ON")
            db.set_progress_handler(lambda: 1, 2000000)
            db.execute("BEGIN")
            rows = db.execute(
                """SELECT id, content, memory_type, source, salience, status,
                          created_at, last_recalled_at, supersedes_memory_id
                   FROM memories WHERE instr(lower(content), lower(?)) > 0
                   ORDER BY created_at DESC, id LIMIT 21 OFFSET ?""",
                (query, offset),
            ).fetchall()
            items = []
            for row in rows[:20]:
                item = dict(row)
                item["evidence"] = [
                    dict(record)
                    for record in db.execute(
                        """SELECT evidence_kind, evidence_ref FROM memory_evidence
                           WHERE memory_id = ? ORDER BY evidence_ref LIMIT 51""",
                        (row["id"],),
                    )
                ]
                item["embeddings"] = [
                    dict(record)
                    for record in db.execute(
                        """SELECT model_name, dimensions, created_at
                           FROM memory_embeddings WHERE memory_id = ?
                           ORDER BY model_name LIMIT 21""",
                        (row["id"],),
                    )
                ]
                items.append(item)
        return self.clean(
            {
                "items": items,
                "more": len(rows) > 20,
                "message": "只读浏览；每页 20 条，证据最多 51 条、缓存描述最多 21 条。",
            }
        )

    def overview(self) -> dict:
        status = {
            key: display_value(value)
            for key, value in asdict(self.manager.status()).items()
        }
        paths = self.manager.paths()
        database = paths.database
        result = {
            "status": status,
            "version": version("evolving-companion-agent"),
            "database": str(database),
            "database_size": f"{database.stat().st_size:,} bytes"
            if database.is_file()
            else "尚未初始化",
            "backups": "目录存在；备份完整性未验证"
            if paths.backups.is_dir()
            else "尚无备份目录",
            "config": "文件存在" if self.manager.env_file.is_file() else "尚未保存",
            "seed": "文件存在"
            if (self.manager.project / "data/characters/si_001.yaml").is_file()
            else "文件缺失",
            "qq": "未验证（未进行 OneBot 状态测试）",
            "activity": "暂无可读取的记忆记录。",
            "memory_count": "暂无数据",
            "today_messages": "暂无数据",
            "affective": self.affective(),
        }
        if self.manager.effective().get("SI_CHAT_TRANSPORT") == "none":
            result["qq"] = "未启用（none）"
        if database.is_file():
            try:
                with closing(
                    sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
                ) as db:
                    db.execute("PRAGMA query_only = ON")
                    db.set_progress_handler(lambda: 1, 2000000)
                    db.execute("BEGIN")
                    result["memory_count"] = db.execute(
                        "SELECT count(*) FROM memories"
                    ).fetchone()[0]
                    result["today_messages"] = db.execute(
                        "SELECT count(*) FROM archive_messages WHERE date(created_at) = date('now')"
                    ).fetchone()[0]
                    latest = db.execute(
                        "SELECT created_at FROM memories ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                    if latest:
                        result["activity"] = f"最近记忆写入：{latest[0]}"
            except sqlite3.Error:
                result["activity"] = "数据库统计暂不可用；未修改数据库。"
        return self.clean(result)
