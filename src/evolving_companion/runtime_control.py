"""Single-instance Linux native control; no Core imports or service manager."""

from collections.abc import Mapping, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
from typing import Literal, Protocol

from evolving_companion.config_env import OperationResult, redact
from evolving_companion.runtime_config import RuntimePaths

RuntimeState = Literal["Running", "Stopped", "Failed", "Unknown"]


@dataclass(frozen=True)
class RuntimeStatus:
    state: RuntimeState
    message: str


class RuntimeController(Protocol):
    def start(self) -> OperationResult: ...
    def stop(self) -> OperationResult: ...
    def restart(self) -> OperationResult: ...
    def status(self) -> RuntimeStatus: ...
    def logs(self) -> OperationResult: ...


def private_open(path: Path, flags: int) -> int:
    """Do not follow symlinks or accept special files as private control files."""
    descriptor = os.open(
        path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Expected regular file")
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class NativeProcessController:
    def __init__(
        self,
        paths: RuntimePaths,
        environment: Mapping[str, str],
        *,
        stop_timeout: float = 15,
    ) -> None:
        self.paths = paths
        self.environment = dict(environment)
        self.stop_timeout = stop_timeout
        # Same DB means same lock/record even when multiple env files are supplied.
        database = paths.database.resolve()
        self.record = database.with_name(database.name + ".process.json")
        self.lock = database.with_name(database.name + ".process.lock")
        self.log = database.with_name(database.name + ".server.log")
        self._child: subprocess.Popen | None = None

    def _command(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "evolving_companion.server",
            "--env-file",
            str(self.paths.env_file.resolve()),
        ]

    def _instance(self) -> dict[str, object]:
        return {
            "project": str(self.paths.project.resolve()),
            "env_file": str(self.paths.env_file.resolve()),
            "database": str(self.paths.database.resolve()),
            "command": self._command(),
        }

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if sys.platform != "linux":
            raise RuntimeError("Native control requires Linux")
        import fcntl

        descriptor = private_open(self.lock, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(descriptor)

    def _read_record(self) -> dict:
        with os.fdopen(private_open(self.record, os.O_RDONLY), "rb") as file:
            contents = file.read(16385)
        if len(contents) > 16384:
            raise ValueError("Oversized record")
        record = json.loads(contents)
        if (
            not isinstance(record, dict)
            or type(record.get("pid")) is not int
            or record["pid"] <= 1
        ):
            raise ValueError("Invalid process record")
        if any(record.get(key) != value for key, value in self._instance().items()):
            raise ValueError("Different instance")
        return record

    def _identity(self, pid: int) -> dict[str, object] | None:
        proc = Path("/proc") / str(pid)
        try:
            fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        except FileNotFoundError:
            return None
        if fields[0] in {"Z", "X"}:
            return None
        # Missing/inaccessible metadata of a live PID is ambiguous, not a stale record.
        return {
            "start_ticks": fields[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "uid": proc.stat().st_uid,
            "cwd": str((proc / "cwd").resolve(strict=True)),
            "argv": (proc / "cmdline").read_bytes().rstrip(b"\0").decode().split("\0"),
        }

    def _inspect(self) -> tuple[RuntimeStatus, dict | None]:
        if not self.record.exists() and not self.record.is_symlink():
            return RuntimeStatus("Stopped", "没有受管理的运行进程。"), None
        if sys.platform != "linux":
            return RuntimeStatus("Unknown", "原生进程身份验证需要 Linux。"), None
        try:
            record = self._read_record()
            if self._child is not None:
                self._child.poll()  # Reap an exited child; never controls an unrelated PID.
            identity = self._identity(record["pid"])
            if identity is None:
                return RuntimeStatus(
                    "Failed", "记录的运行进程已退出；记录已过期。"
                ), record
            if identity != record.get("identity"):
                raise ValueError("Process identity changed")
            if (
                identity["cwd"] != self._instance()["project"]
                or identity["argv"] != self._command()
            ):
                raise ValueError("Unexpected runtime command")
            if identity["uid"] != os.getuid():
                raise ValueError("Different process owner")
            return RuntimeStatus(
                "Running", "已确认进程运行中；不代表聊天已就绪。"
            ), record
        except Exception:
            return RuntimeStatus(
                "Unknown", "无法确认记录中的进程身份；已拒绝控制。"
            ), None

    def status(self) -> RuntimeStatus:
        return self._inspect()[0]

    def _write_record(self, record: dict) -> None:
        # Control operations are locked; concurrent Status may conservatively report Unknown.
        with os.fdopen(
            private_open(self.record, os.O_CREAT | os.O_WRONLY | os.O_TRUNC), "w"
        ) as file:
            json.dump(record, file)
            file.flush()
            os.fsync(file.fileno())

    def _start(self) -> OperationResult:
        status, old = self._inspect()
        if status.state == "Running":
            return OperationResult(True, "进程已在运行，不会重复启动。")
        if status.state == "Unknown":
            return OperationResult(False, status.message)
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            return OperationResult(False, "安全的原生进程控制需要 Linux pidfd 支持。")
        if old is not None:
            self.record.unlink()
        # Reserve first. A failure recording a spawned process must block a second writer.
        self._write_record(self._instance() | {"phase": "launching"})
        try:
            with os.fdopen(
                private_open(self.log, os.O_CREAT | os.O_WRONLY | os.O_APPEND), "ab"
            ) as log:
                self._child = subprocess.Popen(
                    self._command(),
                    cwd=self.paths.project.resolve(),
                    env=self.environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                    shell=False,
                )
        except Exception:
            self.record.unlink()
            raise
        identity = self._identity(self._child.pid)
        if identity is None:
            self._child.poll()
            self.record.unlink()
            return OperationResult(False, "进程在启动期间退出，请查看日志。")
        record = self._instance() | {"pid": self._child.pid, "identity": identity}
        self._write_record(record)
        return OperationResult(
            True,
            "已启动进程，请查看状态和日志；这不代表聊天已就绪。",
        )

    def _stop(self) -> OperationResult:
        status, record = self._inspect()
        if status.state == "Unknown":
            return OperationResult(False, status.message)
        if status.state != "Running":
            if record is not None:
                self.record.unlink()
            return OperationResult(True, "进程已停止；已清理过期记录。")
        assert record is not None
        # Pin the process before revalidation. Never fall back to os.kill(PID).
        descriptor = os.pidfd_open(record["pid"])
        try:
            if self._identity(record["pid"]) != record["identity"]:
                return OperationResult(False, "进程身份已变化；已拒绝停止。")
            signal.pidfd_send_signal(descriptor, signal.SIGINT)
            readable, _, _ = select.select([descriptor], [], [], self.stop_timeout)
            if not readable:
                return OperationResult(
                    False, "停止超时；已保留进程记录，不会强制终止。"
                )
            if self._child is not None:
                self._child.poll()
            self.record.unlink()
            return OperationResult(True, "进程收到 SIGINT 后已退出。")
        finally:
            os.close(descriptor)

    def _operate(self, action: str) -> OperationResult:
        try:
            with self._locked():
                if action == "start":
                    return self._start()
                stopped = self._stop()
                return self._start() if action == "restart" and stopped.ok else stopped
        except Exception as error:
            return OperationResult(
                False,
                f"原生进程操作被拒绝或失败（{type(error).__name__}）；请检查本地权限和进程记录。",
            )

    def start(self) -> OperationResult:
        return self._operate("start")

    def stop(self) -> OperationResult:
        return self._operate("stop")

    def restart(self) -> OperationResult:
        return self._operate("restart")

    def logs(self) -> OperationResult:
        try:
            if not self.log.exists():
                return OperationResult(True, "暂无原生运行日志。")
            with os.fdopen(private_open(self.log, os.O_RDONLY), "rb") as file:
                file.seek(0, os.SEEK_END)
                size = file.tell()
                file.seek(max(0, size - 65536))
                data = file.read(65536)
            lines = data.decode("utf-8", errors="replace").splitlines()
            if size > 65536:
                lines = lines[
                    1:
                ]  # Discard a possibly partial credential-bearing first line.
            return OperationResult(
                True, redact("\n".join(lines[-100:]), self.environment)
            )
        except Exception as error:
            return OperationResult(False, f"无法读取日志（{type(error).__name__}）。")
