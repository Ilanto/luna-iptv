"""English notifications distinguish one episode or day from several."""

import pytest
from test_auto_refresh import scheduler as scheduler_fixture
from test_new_episodes import episode
from test_new_episodes import state as state_fixture

from luna_iptv.accounts import AccountProfile
from luna_iptv.i18n import get_language, set_language

# Reuse the existing controlled services; no network or desktop notifications.
scheduler = scheduler_fixture
state = state_fixture


@pytest.fixture(autouse=True)
def english():
    previous = get_language()
    set_language("en")
    yield
    set_language(previous)


@pytest.mark.parametrize("count", [1, 2])
def test_new_episode_notification_uses_english_plural(state, count):
    if count == 1:
        state.store.upsert_channels("a", [episode("1")])
    state.service.refresh()
    state.complete()
    expected = f"Dark: {count} new episode" + ("s" if count != 1 else "")
    assert state.toasts == [expected]
    assert state.sender.sent == [("New episodes", expected)]


@pytest.mark.parametrize("days", [1, 2])
def test_expiry_notification_uses_english_plural(scheduler, days):
    s = scheduler
    s.store.set_setting("auto_refresh", "off")
    s.store.save_account_profile(
        "a", AccountProfile("active", None, int(s.now[0] + days * 86400), 0, 1, int(s.now[0]))
    )
    s.service.check()
    expected = f"A subscription expires within {days} day" + ("s" if days != 1 else "")
    assert s.notices == [expected]
