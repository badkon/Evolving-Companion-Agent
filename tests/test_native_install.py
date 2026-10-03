"""Native install contract without touching the operator's installation."""

from pathlib import Path
import shutil
import subprocess
import os

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_native_script_has_no_legacy_deployment_and_preserves_data():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    for forbidden in (
        "/opt/si",
        "systemctl",
        "useradd",
        "chown",
        "safe.directory",
        "git reset",
        "rm -rf",
        "local-memory]",
    ):
        assert forbidden not in text
    assert ".venv/bin/python -m pip install -e ." in text
    assert "! -e config/si.env" in text and "! -L config/si.env" in text
    assert "mkdir -p config runtime backups" in text
    assert "SI_BIN_DIR" in text and "SI source launcher" in text


def test_native_bash_syntax():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash unavailable; run bash -n install.sh on Linux")
    subprocess.run(
        [bash, "-n", "install.sh"], cwd=ROOT, check=True, capture_output=True
    )


@pytest.mark.skipif(os.name != "posix", reason="Linux shell install simulation")
def test_native_repeat_preserves_existing_data(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    shutil.copyfile(ROOT / "install.sh", root / "install.sh")
    for folder in (".venv/bin", "config", "runtime", "backups", "data/characters"):
        (root / folder).mkdir(parents=True)
    names = (
        "config/si.env",
        "runtime/test.db",
        "backups/test.db",
        "data/characters/si_001.yaml",
    )
    for name in names:
        (root / name).write_text("preserved", encoding="utf-8")
    (root / "config/si.env.example").write_text("DEEPSEEK_API_KEY=\n")
    fake = root / ".venv/bin/python"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o700)
    environment = dict(os.environ) | {
        "SI_INSTALL_PYTHON": str(fake),
        "SI_BIN_DIR": str(tmp_path / "bin"),
    }
    for _ in range(2):
        subprocess.run(
            ["bash", "install.sh"],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
        )
    for name in names:
        assert (root / name).read_text() == "preserved"
    assert "SI source launcher" in (tmp_path / "bin/si").read_text()
