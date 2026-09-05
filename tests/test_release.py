import re
import tomllib
from pathlib import Path

from luna_iptv import __version__


def test_package_and_runtime_versions_match():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    spec = (root / "packaging/luna-iptv.spec").read_text()
    assert project["project"]["version"] == __version__
    assert re.search(r"^Version:\s*(\S+)", spec, re.M)[1] == __version__
