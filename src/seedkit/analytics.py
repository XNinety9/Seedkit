"""Statistics computed from the current torrent states and their snapshots."""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from seedkit.db import Snapshot, Torrent, TrackerAlias, TrackerRule
from seedkit.rules import HnrResult, HnrStatus, hnr_status, is_problematic

DAY = 86400
WINDOWS = {"24h": DAY, "7d": 7 * DAY, "30d": 30 * DAY, "all": None}
# A download without seeders nor activity for this long is considered dead.
DEAD_AFTER = 7 * DAY
# A torrent still fetching metadata after this long is stuck.
METADATA_STUCK_AFTER = 3600
NO_TRACKER = "(aucun)"


@dataclass
class Catalog:
    """Resolves announce domains to user-facing tracker names, and names to H&R rules."""

    aliases: dict[str, str]
    rules: dict[str, TrackerRule]

    @classmethod
    def load(cls, session: Session) -> "Catalog":
        return cls(
            aliases={a.domain: a.name for a in session.scalars(select(TrackerAlias))},
            rules={r.tracker: r for r in session.scalars(select(TrackerRule))},
        )

    def name(self, domain: str) -> str:
        return self.aliases.get(domain) or domain or NO_TRACKER

    def rule(self, t: Torrent) -> TrackerRule | None:
        return self.rules.get(self.name(t.tracker))

    def hnr(self, t: Torrent, now: int | None = None) -> HnrResult:
        return hnr_status(t, self.rule(t), now)


@dataclass
class Ranked:
    torrent: Torrent
    tracker: str
    uploaded: int  # over the window
    days: float  # time actually covered by the window for this torrent
    efficiency: float  # uploaded / size / day


@dataclass
class TrackerStats:
    name: str
    domains: set[str] = field(default_factory=set)
    count: int = 0
    size: int = 0
    uploaded: int = 0
    downloaded: int = 0
    uploaded_window: int = 0
    statuses: dict = field(default_factory=lambda: defaultdict(int))

    @property
    def ratio(self) -> float | None:
        return self.uploaded / self.downloaded if self.downloaded else None


def active_torrents(session: Session) -> list[Torrent]:
    return list(session.scalars(select(Torrent).where(Torrent.removed_at.is_(None))))


def history_start(session: Session) -> int | None:
    return session.scalar(select(func.min(Snapshot.ts)))


def _baselines(session: Session, since: int) -> dict[str, tuple[int, int]]:
    """Per torrent, the last snapshot at or before `since` (or the first one): (ts, uploaded)."""
    rows = session.execute(
        text(
            """
            SELECT s.hash, s.ts, s.uploaded FROM snapshots s
            JOIN (
                SELECT hash, COALESCE(MAX(CASE WHEN ts <= :since THEN ts END), MIN(ts)) AS bts
                FROM snapshots GROUP BY hash
            ) b ON b.hash = s.hash AND b.bts = s.ts
            """
        ),
        {"since": since},
    )
    return {h: (ts, up) for h, ts, up in rows}


def window_uploads(
    session: Session, torrents: list[Torrent], window: int | None, now: int
) -> dict[str, tuple[int, float]]:
    """Upload over the window and covered days, per torrent hash."""
    result = {}
    since = now - window if window else None
    baselines = _baselines(session, since) if since else {}
    for t in torrents:
        start = t.completion_on or t.added_on
        if since is None or start >= since or t.hash not in baselines:
            # Whole life of the torrent fits in the window.
            result[t.hash] = (t.uploaded, max(now - start, 0) / DAY)
        else:
            ts, up = baselines[t.hash]
            result[t.hash] = (max(t.uploaded - up, 0), max(now - max(ts, since), 0) / DAY)
    return result


def ranking(
    session: Session,
    window: str = "7d",
    now: int | None = None,
    min_days: float = 1.0,
    catalog: Catalog | None = None,
) -> list[Ranked]:
    """Completed torrents sorted by efficiency, best first."""
    now = now or int(time.time())
    catalog = catalog or Catalog.load(session)
    torrents = [t for t in active_torrents(session) if t.amount_left == 0 and t.size > 0]
    uploads = window_uploads(session, torrents, WINDOWS[window], now)
    ranked = []
    for t in torrents:
        uploaded, days = uploads[t.hash]
        if days < min_days:
            continue
        ranked.append(Ranked(t, catalog.name(t.tracker), uploaded, days, uploaded / t.size / days))
    ranked.sort(key=lambda r: r.efficiency, reverse=True)
    return ranked


def _snapshot_deltas_sql(group_by_tracker: bool, bucket: str = "%Y-%m-%d") -> str:
    tracker = ", t.tracker" if group_by_tracker else ""
    return f"""
        SELECT strftime('{bucket}', d.ts, 'unixepoch', 'localtime') AS day{tracker}, SUM(d.delta) FROM (
            SELECT hash, ts, MAX(uploaded - LAG(uploaded) OVER (PARTITION BY hash ORDER BY ts), 0) AS delta
            FROM snapshots WHERE ts >= :since
        ) d {"JOIN torrents t ON t.hash = d.hash" if group_by_tracker else ""}
        WHERE d.delta IS NOT NULL GROUP BY day{tracker} ORDER BY day
    """


def _day_range(days: int, now: int) -> list[str]:
    today = datetime.fromtimestamp(now).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def daily_upload(session: Session, days: int = 30, now: int | None = None) -> list[tuple[str, int]]:
    """Uploaded bytes per local day (every day of the range, zero-filled), from snapshot deltas."""
    now = now or int(time.time())
    rows = dict(session.execute(text(_snapshot_deltas_sql(False)), {"since": now - (days + 1) * DAY}).all())
    if not rows:
        return []
    first = min(rows)
    return [(day, int(rows.get(day, 0))) for day in _day_range(days, now) if day >= first]


def hourly_upload(session: Session, hours: int = 24, now: int | None = None) -> list[tuple[str, int]]:
    """Uploaded bytes per local hour over the last `hours` hours, zero-filled."""
    now = now or int(time.time())
    sql = _snapshot_deltas_sql(False, "%Y-%m-%d %H")
    rows = dict(session.execute(text(sql), {"since": now - (hours + 1) * 3600}).all())
    if not rows:
        return []
    current = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)
    labels = [(current - timedelta(hours=i)).strftime("%Y-%m-%d %H") for i in range(hours - 1, -1, -1)]
    first = min(rows)
    return [(h, int(rows.get(h, 0))) for h in labels if h >= first]


def daily_upload_by_tracker(
    session: Session, days: int = 30, now: int | None = None, catalog: Catalog | None = None, top: int = 4
) -> tuple[list[str], dict[str, list[int]]]:
    """Daily upload per tracker name; trackers beyond `top` are folded into « Autres »."""
    now = now or int(time.time())
    catalog = catalog or Catalog.load(session)
    per_name: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for day, domain, total in session.execute(text(_snapshot_deltas_sql(True)), {"since": now - (days + 1) * DAY}):
        per_name[catalog.name(domain)][day] += int(total)
    if not per_name:
        return [], {}
    first = min(day for series in per_name.values() for day in series)
    labels = [d for d in _day_range(days, now) if d >= first]
    ordered = sorted(per_name, key=lambda n: sum(per_name[n].values()), reverse=True)
    series = {name: [per_name[name].get(d, 0) for d in labels] for name in ordered[:top]}
    if rest := ordered[top:]:
        series["Autres"] = [sum(per_name[n].get(d, 0) for n in rest) for d in labels]
    return labels, series


def tracker_stats(session: Session, now: int | None = None, catalog: Catalog | None = None) -> list[TrackerStats]:
    now = now or int(time.time())
    catalog = catalog or Catalog.load(session)
    torrents = active_torrents(session)
    week = window_uploads(session, torrents, WINDOWS["7d"], now)
    stats: dict[str, TrackerStats] = {}
    for t in torrents:
        name = catalog.name(t.tracker)
        s = stats.setdefault(name, TrackerStats(name))
        s.domains.add(t.tracker)
        s.count += 1
        s.size += t.size
        s.uploaded += t.uploaded
        s.downloaded += t.downloaded
        s.uploaded_window += week[t.hash][0]
        s.statuses[catalog.hnr(t, now).status] += 1
    return sorted(stats.values(), key=lambda s: s.size, reverse=True)


def problem(t: Torrent, hnr: HnrResult, now: int) -> str:
    """Why a torrent needs attention, or an empty string."""
    if hnr.status == HnrStatus.AT_RISK:
        return hnr.reason
    reason = is_problematic(t, now)
    if reason == "en pause":
        return ""  # pausing a torrent that is not under H&R is a legitimate choice
    if reason:
        return reason
    if t.state == "metaDL" and now - t.added_on > METADATA_STUCK_AFTER:
        return "bloqué sur les métadonnées"
    if t.amount_left > 0 and t.num_complete == 0 and now - t.last_activity > DEAD_AFTER:
        return "téléchargement mort (aucun seeder)"
    return ""


def watchlist(
    session: Session, now: int | None = None, catalog: Catalog | None = None
) -> list[tuple[Torrent, str, HnrResult]]:
    """Torrents needing attention, H&R risks first."""
    now = now or int(time.time())
    catalog = catalog or Catalog.load(session)
    items = []
    for t in active_torrents(session):
        hnr = catalog.hnr(t, now)
        if reason := problem(t, hnr, now):
            items.append((t, reason, hnr))
    items.sort(key=lambda i: (i[2].status != HnrStatus.AT_RISK, i[0].name.lower()))
    return items


AGE_BUCKETS = [(7, "< 1 sem."), (30, "< 1 mois"), (90, "< 3 mois"), (365, "< 1 an"), (None, "> 1 an")]


def disk_by_age(session: Session, now: int | None = None) -> list[tuple[str, int, int]]:
    """Occupied space and torrent count per age bucket: (label, size, count)."""
    now = now or int(time.time())
    totals = {label: [0, 0] for _, label in AGE_BUCKETS}
    for t in active_torrents(session):
        age_days = (now - t.added_on) / DAY
        label = next(label for limit, label in AGE_BUCKETS if limit is None or age_days < limit)
        totals[label][0] += t.size
        totals[label][1] += 1
    return [(label, size, count) for label, (size, count) in totals.items()]


def overview(session: Session, now: int | None = None) -> dict:
    now = now or int(time.time())
    torrents = active_torrents(session)
    uploaded = sum(t.uploaded for t in torrents)
    downloaded = sum(t.downloaded for t in torrents)
    result = {
        "count": len(torrents),
        "seeding": sum(1 for t in torrents if t.amount_left == 0),
        "active": sum(1 for t in torrents if t.state in ("uploading", "forcedUP")),
        "size": sum(t.size for t in torrents),
        "uploaded": uploaded,
        "ratio": uploaded / downloaded if downloaded else None,
        "history_start": history_start(session),
    }
    for name in ("24h", "7d", "30d"):
        result[f"uploaded_{name}"] = sum(u for u, _ in window_uploads(session, torrents, WINDOWS[name], now).values())
    return result


def history_days(session: Session, now: int | None = None) -> float:
    start = history_start(session)
    return ((now or time.time()) - start) / DAY if start else 0.0
