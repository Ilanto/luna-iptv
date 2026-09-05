#!/usr/bin/env python3
"""Extract and launch the actual RPM without system installation or root."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
rpm = Path(
    sys.argv[1] if len(sys.argv) > 1 else root / "dist/luna-iptv-0.1.0-1.noarch.rpm"
).resolve()
output = root / "work/qa/package"
output.mkdir(parents=True, exist_ok=True)
metadata = subprocess.check_output(
    ["rpm", "-qp", "--qf", "%{NAME} %{VERSION} %{RELEASE} %{ARCH}", str(rpm)], text=True
)
requires = subprocess.check_output(["rpm", "-qp", "--requires", str(rpm)], text=True)
assert metadata == "luna-iptv 0.1.0 1 noarch"
for requirement in [
    "python3 >= 3.11",
    "python3-pyside6 >= 6.8",
    "python3-python-mpv >= 1.0.8",
    "libmpv2 >= 0.38",
]:
    assert requirement in requires
with tempfile.TemporaryDirectory(prefix="luna-rpm-") as directory:
    extracted = Path(directory)
    producer = subprocess.Popen(["rpm2cpio", str(rpm)], stdout=subprocess.PIPE)
    result = subprocess.run(
        ["cpio", "-idmu", "--quiet"], stdin=producer.stdout, cwd=extracted, capture_output=True
    )
    producer.stdout.close()
    assert producer.wait() == 0 and result.returncode == 0
    package = extracted / "usr/share/luna-iptv"
    for path in (root / "luna_iptv").rglob("*.py"):
        packed = package / path.relative_to(root)
        assert path.read_bytes() == packed.read_bytes(), f"Outdated package module: {path.name}"
    env = dict(
        os.environ,
        PYTHONPATH=str(package),
        LUNA_PACKAGE_ROOT=str(package),
        LUNA_PACKAGE_REPORT=str(output),
    )
    result = subprocess.run(
        [sys.executable, str(extracted / "usr/bin/luna-iptv"), "--version"],
        env=env,
        cwd=extracted,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "Luna IPTV 0.1.0"
    probe = """
import json, os, sys
from pathlib import Path
import luna_iptv
assert str(luna_iptv.__file__).startswith(os.environ['LUNA_PACKAGE_ROOT'])
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import luna_iptv.window as module
class PackageWindow(module.MainWindow):
    def __init__(self, store):
        super().__init__(store)
        QTimer.singleShot(650, self.capture)
    def capture(self):
        out = Path(os.environ['LUNA_PACKAGE_REPORT'])
        self.grab().save(str(out / 'packaged-native.png'))
        (out / 'launch.json').write_text(json.dumps({'platform': QApplication.platformName(), 'DISPLAY_present': bool(os.getenv('DISPLAY')), 'module': str(luna_iptv.__file__), 'visible': self.isVisible()}))
        self.close()
module.MainWindow = PackageWindow
sys.argv = ['luna-iptv', '--data-dir', str(Path.cwd() / 'isolated-library')]
from luna_iptv.app import main
raise SystemExit(main())
"""
    subprocess.run([sys.executable, "-c", probe], env=env, cwd=extracted, check=True, timeout=20)
launch = json.loads((output / "launch.json").read_text())
assert launch["visible"]
report = {
    "metadata": metadata,
    "dependencies": requires.splitlines(),
    "source_bytes_match": True,
    "launcher_version": "Luna IPTV 0.1.0",
    "gui": launch,
    "sha256": hashlib.sha256(rpm.read_bytes()).hexdigest(),
    "success": True,
}
(output / "result.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
