"""The new-version notice."""

import json

from luna_iptv.updates import UpdateChecker, newer, parse_version


class FakeStore:
    def __init__(self, **values):
        self.values = values

    def setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


def test_versions_compare_numerically():
    assert parse_version("v0.19.0") == (0, 19, 0) and parse_version("latest") is None
    assert newer("v0.10.0", "0.9.9") and not newer("v0.9.9", "0.10.0")
    assert not newer("v1.0.0-beta", "0.1.0")


def test_checker_announces_a_newer_release_once_a_day(qt_app):
    answers = []
    payload = json.dumps(
        {"tag_name": "v99.0.0", "html_url": "https://github.com/Ilanto/luna-iptv/releases/x"}
    )
    now = [1_000_000.0]
    store = FakeStore()
    checker = UpdateChecker(store, fetch=lambda callback: callback(payload), clock=lambda: now[0])
    checker.found.connect(lambda version, page: answers.append((version, page)))
    assert checker.check()
    assert answers == [("99.0.0", "https://github.com/Ilanto/luna-iptv/releases/x")]
    assert not checker.check()  # not again the same day
    now[0] += 25 * 3600
    assert checker.check()


def test_checker_respects_the_setting_and_ignores_odd_answers(qt_app):
    calls = []
    checker = UpdateChecker(FakeStore(update_check="off"), fetch=calls.append)
    assert not checker.check() and calls == []
    found = []
    checker = UpdateChecker(FakeStore(), fetch=lambda callback: callback("not json"))
    checker.found.connect(lambda *a: found.append(a))
    checker.check()
    checker = UpdateChecker(
        FakeStore(),
        fetch=lambda callback: callback(json.dumps({"tag_name": "v99.0.0", "prerelease": True})),
    )
    checker.found.connect(lambda *a: found.append(a))
    checker.check()
    assert found == []


def test_unsafe_release_page_falls_back_to_the_project(qt_app):
    found = []
    payload = json.dumps({"tag_name": "v99.0.0", "html_url": "https://evil.test/"})
    checker = UpdateChecker(FakeStore(), fetch=lambda callback: callback(payload))
    checker.found.connect(lambda version, page: found.append(page))
    checker.check()
    assert found == ["https://github.com/Ilanto/luna-iptv/releases/latest"]
