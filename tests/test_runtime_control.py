"""Native control tests never launch the SI Core; Linux uses a synthetic child."""

from contextlib import nullcontext
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from evolving_companion import runtime_control
from evolving_companion.runtime_config import RuntimePaths
from evolving_companion.runtime_control import NativeProcessController


@pytest.fixture
def controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = RuntimePaths(tmp_path, tmp_path / "si.env", tmp_path / "smoke.db", tmp_path)
    control = NativeProcessController(paths, {"DEEPSEEK_API_KEY": "fake-private-key"})
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "getuid", lambda: 123, raising=False)
    monkeypatch.setattr(control, "_locked", nullcontext)
    identities: dict[int, dict[str, object]] = {}
    launches = []
    signals = []

    class Child:
        pid = 400

        def poll(self):
            return None if self.pid in identities else 0

    def launch(command, **options):
        child = Child()
        child.pid += len(launches)
        launches.append((command, options))
        identities[child.pid] = {
            "start_ticks": "123456",
            "boot_id": "test-boot",
            "uid": 123,
            "cwd": str(tmp_path),
            "argv": command,
        }
        return child

    def send(fd, sig):
        signals.append(sig)
        identities.clear()

    monkeypatch.setattr(runtime_control.subprocess, "Popen", launch)
    monkeypatch.setattr(control, "_identity", identities.get)
    monkeypatch.setattr(
        os,
        "pidfd_open",
        lambda pid: os.open(control.lock, os.O_CREAT | os.O_RDWR),
        raising=False,
    )
    monkeypatch.setattr(signal, "pidfd_send_signal", send, raising=False)
    monkeypatch.setattr(runtime_control.select, "select", lambda *args: ([1], [], []))
    return control, identities, launches, signals


def test_start_fixed_command_and_duplicate(controller):
    control, _, launches, _ = controller
    assert control.start().ok
    command, options = launches[0]
    assert command == [
        sys.executable,
        "-m",
        "evolving_companion.server",
        "--env-file",
        str(control.paths.env_file),
    ]
    assert options["cwd"] == control.paths.project
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.STDOUT
    assert options["start_new_session"] and options["close_fds"]
    assert options["shell"] is False
    assert control.status().state == "Running"
    assert control.start().ok and len(launches) == 1
    record = json.loads(control.record.read_text())
    assert record["pid"] == 400 and record["identity"]["start_ticks"] == "123456"
    assert record["env_file"] == str(control.paths.env_file)


def test_stop_restart_and_stale_exit(controller):
    control, identities, launches, signals = controller
    assert control.start().ok
    assert control.restart().ok and len(launches) == 2
    assert signals == [signal.SIGINT]
    assert control.stop().ok and not control.record.exists()
    assert control.status().state == "Stopped"
    assert control.start().ok
    identities.clear()
    assert control.status().state == "Failed"
    assert control.start().ok and len(launches) == 4
    identities.clear()
    assert control.stop().ok and not control.record.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("start_ticks", "reused"),
        ("boot_id", "different-boot"),
        ("argv", ["other-program"]),
        ("uid", 999),
    ],
)
def test_pid_reuse_refuses_all_control(controller, field, value):
    control, identities, launches, signals = controller
    assert control.start().ok
    identities[400][field] = value
    assert control.status().state == "Unknown"
    assert not control.stop().ok and not control.restart().ok and not control.start().ok
    assert not signals and len(launches) == 1 and control.record.exists()


def test_corrupt_and_different_instance_unknown(controller):
    control, _, launches, _ = controller
    control.record.write_text("not json")
    assert control.status().state == "Unknown" and not control.start().ok
    control.record.unlink()
    assert control.start().ok
    data = json.loads(control.record.read_text())
    data["env_file"] = "different.env"
    control.record.write_text(json.dumps(data))
    assert not control.stop().ok and len(launches) == 1


def test_timeout_does_not_restart_or_kill(controller, monkeypatch):
    control, _, launches, signals = controller
    assert control.start().ok
    monkeypatch.setattr(
        signal, "pidfd_send_signal", lambda fd, sig: signals.append(sig)
    )
    monkeypatch.setattr(runtime_control.select, "select", lambda *args: ([], [], []))
    result = control.restart()
    assert not result.ok and "停止超时" in result.message
    assert signals == [signal.SIGINT] and len(launches) == 1
    assert control.record.exists() and control.status().state == "Running"


def test_identity_rechecked_after_pidfd_open(controller, monkeypatch):
    control, identities, _, signals = controller
    assert control.start().ok
    open_fd = os.pidfd_open

    def changed(pid):
        fd = open_fd(pid)
        identities[pid] = identities[pid] | {"start_ticks": "changed"}
        return fd

    monkeypatch.setattr(os, "pidfd_open", changed)
    assert not control.stop().ok and not signals


def test_launch_failure_and_ambiguous_reservation(controller, monkeypatch):
    control, _, launches, _ = controller

    def fail(*args, **kwargs):
        raise PermissionError("fake-private-key")

    real_launch = runtime_control.subprocess.Popen
    monkeypatch.setattr(runtime_control.subprocess, "Popen", fail)
    result = control.start()
    assert not result.ok and "fake-private-key" not in result.message
    assert not control.record.exists()
    monkeypatch.setattr(runtime_control.subprocess, "Popen", real_launch)
    monkeypatch.setattr(control, "_identity", fail)
    assert not control.start().ok
    assert control.status().state == "Unknown"
    assert not control.start().ok and len(launches) == 1


def test_locked_operation_refuses_concurrent_launch(controller, monkeypatch):
    control, _, launches, _ = controller

    def busy():
        raise BlockingIOError("locked")

    monkeypatch.setattr(control, "_locked", busy)
    assert not control.start().ok and not launches


def test_dead_during_launch_and_unsupported_pidfd(controller, monkeypatch):
    control, _, launches, _ = controller
    monkeypatch.setattr(control, "_identity", lambda pid: None)
    assert not control.start().ok and not control.record.exists()
    assert len(launches) == 1
    monkeypatch.delattr(os, "pidfd_open")
    assert not control.start().ok and len(launches) == 1


def test_proc_identity_missing_vs_unverifiable(tmp_path, monkeypatch):
    control = NativeProcessController(
        RuntimePaths(tmp_path, tmp_path / "env", tmp_path / "db", tmp_path), {}
    )
    proc = tmp_path / "proc/400"
    proc.mkdir(parents=True)
    boot = tmp_path / "proc/sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("synthetic-boot\n")
    monkeypatch.setattr(
        runtime_control, "Path", lambda value: tmp_path / str(value).lstrip("/")
    )
    assert control._identity(400) is None  # Missing /proc stat: PID gone.
    fields = ["S"] + ["0"] * 18 + ["12345"]
    (proc / "stat").write_text("400 (command with ) spaces) " + " ".join(fields))
    with pytest.raises(FileNotFoundError):
        control._identity(400)  # Missing live metadata must not be treated as exited.
    fields[0] = "Z"
    (proc / "stat").write_text("400 (command) " + " ".join(fields))
    assert control._identity(400) is None


def test_bounded_logs_and_redaction(controller):
    control, _, _, _ = controller
    assert control.logs().ok
    control.log.write_text(
        "x" * 80000
        + "\n"
        + "line\n" * 200
        + "[bold]fake-private-key\x1b\x00\nAuthorization: fake-old-key\n",
        encoding="utf-8",
    )
    result = control.logs()
    assert result.ok and len(result.message.encode()) <= 65536
    assert len(result.message.splitlines()) <= 100
    assert (
        "fake-private-key" not in result.message
        and "fake-old-key" not in result.message
    )
    assert "\x1b" not in result.message and "\x00" not in result.message
    assert "[bold]" in result.message  # Textual renders literally, not as markup.


@pytest.mark.skipif(
    sys.platform != "linux", reason="Real /proc, flock and pidfd require Linux"
)
def test_linux_detached_child_reopens_and_stops(tmp_path: Path):
    """Parent launcher exits; a new controller verifies and stops its synthetic child."""
    child_code = "import signal,time; signal.signal(signal.SIGINT, lambda *a: exit(0)); print('ready',flush=True); time.sleep(60)"
    command = [sys.executable, "-u", "-c", child_code]
    paths = RuntimePaths(
        tmp_path, tmp_path / "fake.env", tmp_path / "fake.db", tmp_path
    )
    launcher = (
        "import os; from pathlib import Path; "
        "from evolving_companion.runtime_control import NativeProcessController; "
        "from evolving_companion.runtime_config import RuntimePaths; "
        f"p=Path({str(tmp_path)!r}); c=NativeProcessController(RuntimePaths(p,p/'fake.env',p/'fake.db',p),os.environ); "
        f"c._command=lambda: {command!r}; assert c.start().ok"
    )
    subprocess.run([sys.executable, "-c", launcher], check=True, timeout=10)
    control = NativeProcessController(paths, os.environ, stop_timeout=5)
    control._command = lambda: command
    try:
        assert control.status().state == "Running"
        until = time.monotonic() + 5
        while "ready" not in control.logs().message and time.monotonic() < until:
            time.sleep(0.02)
        assert "ready" in control.logs().message
        assert control.log.stat().st_mode & 0o777 == 0o600
        assert control.record.stat().st_mode & 0o777 == 0o600
        # Real flock excludes another Manager during the operation.
        other = NativeProcessController(paths, os.environ)
        with control._locked():
            assert not other.start().ok
        assert control.stop().ok
        assert control.status().state == "Stopped" and not paths.database.exists()
    finally:
        control.stop()  # Only verified synthetic process; never signals a real SI.
