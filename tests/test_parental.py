"""PINs and global locks stay private and survive catalogue refreshes."""

import hashlib
import importlib
import subprocess
import sys

import pytest

from luna_iptv.backup import apply_backup, export_backup
from luna_iptv.models import Channel
from luna_iptv.storage import Store


@pytest.fixture
def parental():
    return importlib.import_module("luna_iptv.parental")


@pytest.mark.parametrize("pin", ["0000", "1234", "12345678"])
def test_pin_round_trip_uses_random_salt_and_specified_kdf(parental, pin):
    assert parental.valid_pin(pin)
    stored = parental.hash_pin(pin)
    algorithm, iterations, salt, digest = stored.split("$")
    assert algorithm == "pbkdf2_sha256"
    assert iterations == "200000"
    assert len(bytes.fromhex(salt)) == 16
    assert bytes.fromhex(digest) == hashlib.pbkdf2_hmac(
        "sha256", pin.encode("ascii"), bytes.fromhex(salt), 200_000
    )
    assert parental.verify_pin(pin, stored)
    assert not parental.verify_pin("9999", stored)
    assert parental.hash_pin(pin) != stored


@pytest.mark.parametrize(
    "pin", ["", "123", "123456789", "１２３４", "١٢٣٤", "12a4", "1234\n", " 1234"]
)
def test_invalid_pins(parental, pin):
    assert not parental.valid_pin(pin)
    with pytest.raises(ValueError):
        parental.hash_pin(pin)
    assert not parental.verify_pin(pin, None)


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "",
        "plain",
        "sha256$200000$00$00",
        "pbkdf2_sha256$no$00$00",
        "pbkdf2_sha256$0$00$00",
        "pbkdf2_sha256$-1$00$00",
        "pbkdf2_sha256$99999999999999999999999$00$00",
        "pbkdf2_sha256$200000$zz$00",
        "pbkdf2_sha256$200000$$",
        "pbkdf2_sha256$200000$00$00$extra",
        "pbkdf2_sha256$200000$" + "00" * 16 + "$00",
        "pbkdf2_sha256$200000$" + "00" * 15 + "$" + "00" * 32,
    ],
)
def test_malformed_stored_pin_is_false(parental, stored):
    assert not parental.verify_pin("1234", stored)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("XXX", True),
        ("TR | ADULT", True),
        ("Adults HD", True),
        ("For Adults", True),
        ("18+", True),
        ("+18", True),
        ("TR (18+) HD", True),
        ("TR +18 HD", True),
        ("Yetişkin", True),
        ("YETİŞKİN", True),
        ("YETISKIN", True),
        ("YETIŞKIN", True),
        ("Erotik", True),
        ("EROTIC", True),
        ("Porn", True),
        ("Porno", True),
        ("", False),
        ("Sussex", False),
        ("Essex", False),
        ("Adultère", False),
        ("Euro 2018", False),
        ("TRT 1", False),
        ("18", False),
        ("118+", False),
        ("+180", False),
        ("2018+", False),
        ("Adulting", False),
        ("xxxtra", False),
        ("Haber", False),
        ("Çocuk", False),
        ("Spor", False),
    ],
)
def test_adult_group_word_boundaries(parental, name, expected):
    assert parental.is_adult_group(name) is expected


def test_parental_module_does_not_import_qt():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import luna_iptv.parental; "
            "assert not any(m.startswith('PySide6') for m in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "parental.sqlite3")
    value.save_source({"id": "home", "type": "xtream", "name": "Ev"})
    value.replace_channels(
        "home",
        [
            Channel("one", "Bir", "", group="XXX", provider_key="live:1"),
            Channel("two", "İki", "", group="XXX", provider_key="live:2"),
            Channel("three", "Üç", "", group="Adult", provider_key="live:3"),
            Channel("four", "Dört", "", group="haber", provider_key="live:4"),
            Channel("five", "Beş", "", group=""),
        ],
    )
    yield value
    value.close()


def test_auto_locks_respect_explicit_unlocks_and_count_new_groups(store):
    assert store.locked_groups() == set()
    store.set_group_locked("home", "XXX", False)
    assert store.lock_adult_groups() == 1
    assert store.locked_groups() == {("home", "Adult")}
    assert store.lock_adult_groups() == 0
    store.set_group_locked("home", "Adult", False)
    assert store.lock_adult_groups() == 0
    assert store.locked_groups() == set()
    store.set_group_locked("home", "XXX", True)
    store.use_profile(store.create_profile("Diğer", "#123456"))
    assert store.locked_groups() == {("home", "XXX")}
    assert store.channel_groups() == [
        ("home", "Adult", 1),
        ("home", "haber", 1),
        ("home", "XXX", 2),
    ]
    store.remove_source("home")
    assert store.locked_groups() == set()


@pytest.mark.parametrize("legacy", [False, True])
def test_channel_lock_survives_provider_id_reconciliation(store, legacy):
    if legacy:
        with store._db:
            store._db.execute(
                "UPDATE channels SET provider_key='', url='https://example.test/live/u/p/1.ts' "
                "WHERE id='home:one'"
            )
    store.set_channel_locked("home:one", True)
    store.set_channel_locked("home:two", True)
    store.replace_channels(
        "home", [Channel("new-id", "Yeni", "", group="XXX", provider_key="live:1")]
    )
    assert store.locked_channels() == {"home:one"}
    assert store.channels()[0].id == "home:one"
    store.use_profile(store.create_profile("Diğer", "#123456"))
    assert store.locked_channels() == {"home:one"}
    store.set_channel_locked("home:one", False)
    assert store.locked_channels() == set()


def test_secrets_and_locks_persist_but_never_participate_in_backups(store):
    assert store.pin_hash() is None
    store.set_pin_hash("test-hash")
    store.set_channel_locked("home:one", True)
    store.set_group_locked("home", "XXX", False)
    store.set_group_locked("home", "Adult", True)
    data = export_backup(store, include_credentials=True)
    assert not {"secrets", "group_locks", "channel_locks"} & data.keys()
    assert "test-hash" not in str(data)
    apply_backup(store, data)
    assert store.pin_hash() == "test-hash"
    assert store.locked_channels() == {"home:one"}
    assert store.locked_groups() == {("home", "Adult")}
    assert store.lock_adult_groups() == 0
    reopened = Store(store.path)
    try:
        assert reopened.pin_hash() == "test-hash"
        assert reopened.locked_channels() == {"home:one"}
        assert reopened.locked_groups() == {("home", "Adult")}
        reopened.set_pin_hash(None)
        assert reopened.pin_hash() is None
    finally:
        reopened.close()
