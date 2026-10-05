from seedkit import analytics
from seedkit.collector import store
from seedkit.db import Torrent
from tests.conftest import DAY, NOW, torrent

GB = 1024**3


def test_ranking_uses_window_deltas(session):
    a, b = "a" * 40, "b" * 40
    # 10 days ago: a had already uploaded a lot, b nothing.
    store(session, [torrent(a, uploaded=100 * GB), torrent(b, uploaded=0)], now=NOW - 10 * DAY)
    store(session, [torrent(a, uploaded=101 * GB), torrent(b, uploaded=20 * GB)], now=NOW)

    week = analytics.ranking(session, "7d", now=NOW)
    assert [r.torrent.hash for r in week] == [b, a]
    assert week[0].uploaded == 20 * GB
    assert week[0].days == 7

    lifetime = analytics.ranking(session, "all", now=NOW)
    assert [r.torrent.hash for r in lifetime] == [a, b]


def test_window_with_short_history_uses_available_snapshots(session):
    old = {"added_on": NOW - 90 * DAY, "completion_on": NOW - 90 * DAY}
    store(session, [torrent(uploaded=10 * GB, **old)], now=NOW - 2 * DAY)
    store(session, [torrent(uploaded=14 * GB, **old)], now=NOW)
    [r] = analytics.ranking(session, "30d", now=NOW)
    assert r.uploaded == 4 * GB
    assert r.days == 2


def test_recent_torrent_counts_whole_upload(session):
    store(session, [torrent(uploaded=5 * GB, added_on=NOW - 3 * DAY, completion_on=NOW - 3 * DAY)], now=NOW)
    [r] = analytics.ranking(session, "7d", now=NOW)
    assert (r.uploaded, r.days) == (5 * GB, 3)


def test_daily_upload_ignores_removed_and_negative_deltas(session):
    store(session, [torrent("a" * 40, uploaded=GB), torrent("b" * 40, uploaded=GB)], now=NOW - DAY)
    store(session, [torrent("a" * 40, uploaded=3 * GB)], now=NOW)
    days = analytics.daily_upload(session, now=NOW)
    assert sum(v for _, v in days) == 2 * GB


def test_removed_torrents_are_flagged(session):
    store(session, [torrent("a" * 40), torrent("b" * 40)], now=NOW - DAY)
    store(session, [torrent("a" * 40)], now=NOW)
    assert session.get(Torrent, "b" * 40).removed_at == NOW
    assert [t.hash for t in analytics.active_torrents(session)] == ["a" * 40]


def test_watchlist(session):
    store(
        session,
        [
            torrent("a" * 40),
            torrent("b" * 40, tracker_msg="Unregistered torrent"),
            torrent("c" * 40, state="stoppedUP"),  # paused without rule: legitimate
            torrent("d" * 40, amount_left=1, num_complete=0, last_activity=NOW - 10 * DAY),
        ],
        now=NOW,
    )
    reasons = {t.hash[0]: reason for t, reason, _ in analytics.watchlist(session, now=NOW)}
    assert reasons == {"b": "non enregistré sur le tracker", "d": "téléchargement mort (aucun seeder)"}
