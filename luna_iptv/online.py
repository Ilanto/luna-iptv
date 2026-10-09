"""Small, bounded online requests and atomic cache files; never log private values."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .network import NetworkError

JSON_LIMIT = 2 * 1024 * 1024


class OnlineError(NetworkError):
    def __init__(self, status=0):
        self.status = status
        super().__init__(
            "Günlük indirme hakkı doldu"
            if status in (406, 429)
            else "Çevrimiçi servise erişilemedi. Anahtarı ve bağlantıyı kontrol edin."
        )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # API keys and login bodies must never follow a redirect to another host.
        return None


def request(url, *, headers=None, data=None, max_bytes=JSON_LIMIT):
    """Return bounded bytes; HTTP status survives, response bodies and URLs do not."""
    try:
        body = json.dumps(data).encode() if data is not None else None
        req = Request(
            url,
            data=body,
            headers={
                "User-Agent": "Luna-IPTV v0.19.0",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body else {}),
                **(headers or {}),
            },
        )
        with build_opener(_NoRedirect()).open(req, timeout=15) as response:
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise OnlineError()
        return raw
    except HTTPError as exc:
        raise OnlineError(exc.code) from None
    except (URLError, OSError, ValueError):
        raise OnlineError() from None


def read_json(path, limit=JSON_LIMIT):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(limit + 1)
        return json.loads(raw) if len(raw) <= limit else None
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def write_bytes(path, raw):
    """Replace one cache entry without leaving a partially written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path, value):
    write_bytes(path, json.dumps(value, ensure_ascii=False).encode())
