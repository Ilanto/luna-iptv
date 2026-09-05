import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from luna_iptv.network import NetworkError, XtreamClient, fetch, load_m3u


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit

            query = parse_qs(urlsplit(self.path).query)
            if self.path.startswith("/large"):
                body = b"x" * 2048
            elif self.path.startswith("/list"):
                body = b'#EXTM3U\n#EXTINF:-1 tvg-id="one",Test Channel\nstream.ts\n'
            else:
                action = query.get("action", [""])[0]
                body = json.dumps(
                    {
                        "": {"user_info": {"auth": 1}},
                        "get_live_categories": [{"category_id": "1", "category_name": "News"}],
                        "get_vod_categories": [],
                        "get_series_categories": [],
                        "get_live_streams": [
                            {
                                "stream_id": 12,
                                "name": "One",
                                "category_id": "1",
                                "epg_channel_id": "one",
                            }
                        ],
                        "get_vod_streams": [
                            {"stream_id": 13, "name": "Movie", "container_extension": "mp4"}
                        ],
                        "get_series": [{"series_id": 14, "name": "Series"}],
                        "get_series_info": {
                            "episodes": {
                                "2": [
                                    {
                                        "id": 88,
                                        "title": "Episode",
                                        "episode_num": 1,
                                        "container_extension": "mkv",
                                    }
                                ]
                            }
                        },
                    }.get(action, {})
                ).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_size_limit_and_bad_scheme(server):
    with pytest.raises(NetworkError):
        fetch(server + "/large", max_bytes=100)
    with pytest.raises(NetworkError):
        fetch("file:///etc/passwd")


def test_remote_m3u_resolves_urls(server):
    result = load_m3u(server + "/list")
    assert result.channels[0].url == server + "/stream.ts"


def test_xtream_catalog_and_episodes(server):
    client = XtreamClient(server, "u /", "secret&?")
    channels = client.catalog().channels
    assert [c.kind for c in channels] == ["live", "movie", "series"]
    assert channels[0].group == "News"
    assert "/live/u%20%2F/secret%26%3F/12.ts" in channels[0].url
    assert channels[2].series_id == "14"
    episodes = client.episodes("14")
    assert "/series/" in episodes[0].url
    assert episodes[0].group == "Sezon 2"


def test_error_does_not_leak_password(monkeypatch):
    import luna_iptv.network as n

    monkeypatch.setattr(n, "fetch", lambda *a, **k: b'{"user_info":{"auth":0}}')
    with pytest.raises(NetworkError) as info:
        XtreamClient("http://provider.test", "user", "SECRET").catalog()
    assert "SECRET" not in str(info.value)


def test_malformed_account_rejected_cleanly(monkeypatch):
    import luna_iptv.network as n

    monkeypatch.setattr(n, "fetch", lambda *a, **k: b'{"user_info":[]}')
    with pytest.raises(NetworkError):
        XtreamClient("http://provider.test", "u", "p").catalog()


def test_redirect_base_is_used(monkeypatch):
    import io

    import luna_iptv.network as n

    class Response(io.BytesIO):
        def geturl(self):
            return "https://provider.test/new/catalog.m3u"

    monkeypatch.setattr(
        n, "urlopen", lambda *a, **k: Response(b"#EXTM3U\n#EXTINF:-1,News\nnews.ts\n")
    )
    assert (
        load_m3u("https://provider.test/old").channels[0].url == "https://provider.test/new/news.ts"
    )
