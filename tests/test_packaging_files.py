"""Check Flatpak identity, desktop integration and sandbox permissions."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "io.github.ilanto.LunaIPTV"
PACKAGING = ROOT / "packaging" / "flatpak"


def test_flatpak_manifest():
    yaml = pytest.importorskip("yaml")
    manifest = yaml.safe_load((PACKAGING / f"{APP_ID}.yml").read_text())
    assert manifest["app-id"] == APP_ID
    assert manifest["runtime-version"] == manifest["base-version"] == "6.8"
    assert manifest["base"] == "io.qt.PySide.BaseApp"
    assert manifest["command"] == "luna-iptv"
    commands = "\n".join(manifest["modules"][-1]["build-commands"])
    assert f"/app/share/applications/{APP_ID}.desktop" in commands
    assert (ROOT / "packaging" / "luna-iptv.desktop").is_file()
    permissions = manifest["finish-args"]
    assert "--own-name=org.mpris.MediaPlayer2.luna_iptv" in permissions
    assert "--filesystem=xdg-videos/Luna:create" in permissions
    for permission in permissions:
        assert not permission.startswith(("--filesystem=home", "--filesystem=host"))


def test_flatpak_metainfo():
    component = ET.parse(PACKAGING / f"{APP_ID}.metainfo.xml").getroot()
    assert component.get("type") == "desktop-application"
    assert component.findtext("id") == APP_ID
    assert component.findtext("name") == "Luna IPTV"
    assert component.findtext("summary") == "Kişisel IPTV istemcisi"
    assert component.findtext("launchable[@type='desktop-id']") == f"{APP_ID}.desktop"
    assert component.find("content_rating").get("type") == "oars-1.1"
    assert component.findall("releases/release")
