import pytest

from seedkit import notify
from seedkit.collector import store
from seedkit.config import Settings
from tests.conftest import NOW, torrent


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(
        notify, "send", lambda settings, title, message, urgent=False: messages.append((title, message))
    )
    return messages


def enabled(settings):
    return Settings(
        **(settings.model_dump() | {"seedkit_ntfy_url": "https://ntfy.invalid/x", "seedkit_summary_hour": 9})
    )


def test_alerts_are_sent_once_and_again_after_recovery(session, settings, sent):
    s = enabled(settings)
    store(session, [torrent(tracker_msg="Unregistered torrent")], now=NOW)
    assert len(notify.check_alerts(session, s, NOW)) == 1
    assert notify.check_alerts(session, s, NOW) == []
    store(session, [torrent()], now=NOW + 1)
    notify.check_alerts(session, s, NOW + 1)
    store(session, [torrent(tracker_msg="Unregistered torrent")], now=NOW + 2)
    assert len(notify.check_alerts(session, s, NOW + 2)) == 1
    assert len(sent) == 2


def test_nothing_sent_when_disabled(session, settings, sent):
    store(session, [torrent(tracker_msg="Unregistered torrent")], now=NOW)
    assert notify.check_alerts(session, settings, NOW) == []
    assert not notify.maybe_send_summary(session, settings, NOW)
    assert sent == []


def test_daily_summary_once_per_day(session, settings, sent, monkeypatch):
    s = enabled(settings)
    store(session, [torrent()], now=NOW)
    monkeypatch.setattr(
        notify.time, "localtime", lambda ts=None: __import__("time").struct_time((2027, 1, 15, 10, 0, 0, 4, 15, 0))
    )
    assert notify.maybe_send_summary(session, s, NOW)
    assert not notify.maybe_send_summary(session, s, NOW + 60)
    assert "Upload 24 h" in sent[0][1]
