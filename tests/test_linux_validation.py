"""Temporary-only validation tests; no host account/systemd or external downloads."""

import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from evolving_companion.linux_validation import check_backup_restore, check_owned_path

REPO = Path(__file__).resolve().parents[1]


def test_backup_restore_probe_leaves_real_runtime_untouched(tmp_path: Path) -> None:
    runtime = tmp_path / "si_001.db"
    runtime.write_bytes(b"must not be opened or changed")
    backups = tmp_path / "backups"
    backups.mkdir()
    check_backup_restore(backups, "dccb85ed-04ce-4d8f-a135-f60387eaad9e")
    assert runtime.read_bytes() == b"must not be opened or changed"
    assert not list(backups.iterdir())


@pytest.mark.parametrize("uid,gid,mode", [(1, 2, 0o600), (2, 2, 0o600), (1, 2, 0o644)])
def test_permission_checks_reject_wrong_owner_or_public_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uid: int, gid: int, mode: int
) -> None:
    path = tmp_path / "config"
    path.write_text("", encoding="utf-8")
    info = os.stat_result((stat.S_IFREG | mode, 0, 0, 1, uid, gid, 0, 0, 0, 0))
    monkeypatch.setattr(Path, "stat", lambda self, **kwargs: info)
    if (uid, gid, mode) == (1, 2, 0o600):
        check_owned_path(path, 1, 2, 0o600, directory=False)
    else:
        with pytest.raises(PermissionError):
            check_owned_path(path, 1, 2, 0o600, directory=False)


def test_installer_bootstrap_and_launcher_contract() -> None:
    installer = (REPO / "deploy/install.sh").read_text(encoding="utf-8")
    assert "sha256sum -c -" in installer
    assert "0.12.22/uv-x86_64-unknown-linux-gnu.tar.gz" in installer
    assert "UV_CONCURRENT_BUILDS=1" in installer
    assert "UV_CONCURRENT_INSTALLS=1" in installer
    assert "git clone --" in installer and "git pull" not in installer
    assert "status --porcelain" in installer
    assert "systemctl daemon-reload" in installer
    assert "systemctl start" not in installer and "systemctl enable" not in installer
    assert "--offline" in installer
    launcher = (REPO / "deploy/si.sh").read_text(encoding="utf-8")
    assert 'exec /opt/si/app/.venv/bin/si "$@"' in launcher
    assert "deploy-check|health|backup|restore" in launcher


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash is not on PATH; syntax checked separately",
)
def test_deploy_shell_syntax() -> None:
    for script in (REPO / "deploy").glob("*.sh"):
        subprocess.run(["bash", "-n", str(script)], check=True, timeout=10)
