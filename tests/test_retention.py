from sqlalchemy import select

from seedkit import analytics, retention
from seedkit.collector import store
from seedkit.db import Snapshot
from tests.conftest import DAY, NOW, torrent

GB = 1024**3


def test_compaction_keeps_totals(session, settings):
    # 120 days of collections every 2 hours, uploading 1 GB per collection.
    start = NOW - 120 * DAY
    for i, ts in enumerate(range(start, NOW + 1, 7200)):
        store(session, [torrent(uploaded=i * GB, added_on=start - DAY, completion_on=start - DAY)], now=ts)
    before = session.query(Snapshot).count()
    week_before = analytics.overview(session, NOW)["uploaded_7d"]
    removed = retention.compact(session, settings, now=NOW)
    after = session.query(Snapshot).count()
    assert removed == before - after > 0
    assert analytics.overview(session, NOW)["uploaded_7d"] == week_before
    old = list(session.scalars(select(Snapshot.ts).where(Snapshot.ts < NOW - 90 * DAY)))
    assert len(old) <= 31  # one per day
