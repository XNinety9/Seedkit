"""Data shaped for the pages and the JSON API (shared by the web UI and the TUI)."""

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from seedkit import analytics
from seedkit.analytics import Catalog
from seedkit.db import ActionLog, Torrent
from seedkit.i18n import t as tr
from seedkit.rules import HNR_LABELS, HnrResult, HnrStatus

MAX_SERIES = 4  # categorical slots before folding into « Autres »
OTHERS = "Autres"
SCATTER_FLOOR = 1024**2  # 1 MiB/day: below this, a torrent is drawn on the floor line


def default_window(session: Session, now: int | None = None) -> str:
    """The most meaningful ranking window given how much history has been collected."""
    days = analytics.history_days(session, now)
    return "7d" if days >= 7 else "24h" if days >= 1 else "all"


@dataclass
class Palette:
    """Stable color slot per tracker name, by occupied size: color follows the entity, never its rank."""

    slots: dict[str, int]

    @classmethod
    def build(cls, stats: list[analytics.TrackerStats]) -> "Palette":
        return cls({s.name: i for i, s in enumerate(stats[:MAX_SERIES])})

    def slot(self, name: str) -> int:
        return self.slots.get(name, MAX_SERIES)

    def label(self, name: str) -> str:
        return name if name in self.slots else OTHERS


def _display(name: str) -> str:
    return tr(OTHERS) if name == OTHERS else name


def _fold(values: dict[str, int], palette: Palette) -> list[dict]:
    folded: dict[str, int] = {}
    for name, v in values.items():
        folded[palette.label(name)] = folded.get(palette.label(name), 0) + v
    items = [
        {"name": _display(n), "value": v, "c": palette.slot(n) if n != OTHERS else MAX_SERIES}
        for n, v in folded.items()
    ]
    return sorted(items, key=lambda i: i["c"])


def dashboard(session: Session, window: str | None = None, now: int | None = None) -> dict:
    now = now or int(time.time())
    catalog = Catalog.load(session)
    window = window or default_window(session, now)
    stats = analytics.tracker_stats(session, now, catalog)
    palette = Palette.build(stats)
    overview = analytics.overview(session, now)
    ranked = analytics.ranking(session, window, now, catalog=catalog)
    watch = analytics.watchlist(session, now, catalog)

    labels, by_tracker = analytics.daily_upload_by_tracker(session, 30, now, catalog, top=len(stats) or 1)
    daily_values: dict[str, list[int]] = {}
    for name, values in by_tracker.items():
        key = palette.label(name)
        daily_values[key] = [a + b for a, b in zip(daily_values.get(key, [0] * len(values)), values, strict=True)]
    daily_series = [
        {"name": _display(n), "data": v, "c": palette.slot(n) if n != OTHERS else MAX_SERIES}
        for n, v in daily_values.items()
    ]
    daily_series.sort(key=lambda s: s["c"])

    hourly = analytics.hourly_upload(session, 24, now)
    daily_total = analytics.daily_upload(session, 30, now)

    status_totals = {s: 0 for s in HnrStatus}
    for s in stats:
        for status, n in s.statuses.items():
            status_totals[status] += n

    return {
        "window": window,
        "overview": overview,
        "history_days": analytics.history_days(session, now),
        "trackers": stats,
        "palette": palette,
        "top": ranked[:6],
        "flop": list(reversed(ranked[-6:])) if ranked else [],
        "watch": watch,
        "status_totals": status_totals,
        "charts": {
            "daily": {"labels": labels, "series": daily_series},
            "hourly": {"labels": [h for h, _ in hourly], "values": [v for _, v in hourly], "unit": "bytes"},
            "daily_total": {
                "labels": [d for d, _ in daily_total],
                "values": [v for _, v in daily_total],
                "unit": "bytes",
            },
            "disk": {
                "labels": [i["name"] for i in _fold({s.name: s.size for s in stats}, palette)],
                "values": [i["value"] for i in _fold({s.name: s.size for s in stats}, palette)],
                "colors": [i["c"] for i in _fold({s.name: s.size for s in stats}, palette)],
            },
            "age": _age_chart(session, now),
            "scatter": scatter(session, window, now, catalog, palette),
        },
    }


def _age_chart(session: Session, now: int) -> dict:
    buckets = analytics.disk_by_age(session, now)
    return {
        "labels": [tr(b[0]) for b in buckets],
        "values": [b[1] for b in buckets],
        "counts": [b[2] for b in buckets],
        "unit": "bytes",
    }


def scatter(session: Session, window: str, now: int, catalog: Catalog, palette: Palette) -> dict:
    datasets: dict[str, dict] = {}
    for r in analytics.ranking(session, window, now, min_days=0.25, catalog=catalog):
        name = palette.label(r.tracker)
        ds = datasets.setdefault(
            name, {"name": _display(name), "c": palette.slot(name) if name != OTHERS else MAX_SERIES, "points": []}
        )
        per_day = r.uploaded / r.days if r.days else 0
        ds["points"].append({"x": r.torrent.size, "y": max(per_day, SCATTER_FLOOR), "n": r.torrent.name})
    return {
        "datasets": sorted(datasets.values(), key=lambda d: d["c"]),
        "floor": SCATTER_FLOOR,
        "window": window,
    }


# Torrents list -------------------------------------------------------------

SORTS = {
    "name": lambda t, h: t.name.lower(),
    "size": lambda t, h: t.size,
    "ratio": lambda t, h: t.ratio,
    "uploaded": lambda t, h: t.uploaded,
    "seed": lambda t, h: t.seeding_time,
    "seeders": lambda t, h: t.num_complete,
    "added": lambda t, h: t.added_on,
    "hnr": lambda t, h: (list(HnrStatus).index(h.status), h.progress or 0),
}


@dataclass
class TorrentRow:
    torrent: Torrent
    tracker: str
    hnr: HnrResult
    slot: int


def torrents(
    session: Session,
    q: str = "",
    tracker: str = "",
    status: str = "",
    sort: str = "added",
    desc: bool = True,
    page: int = 1,
    per_page: int = 50,
    now: int | None = None,
) -> dict:
    now = now or int(time.time())
    catalog = Catalog.load(session)
    palette = Palette.build(analytics.tracker_stats(session, now, catalog))
    words = q.casefold().split()
    rows = []
    for t in analytics.active_torrents(session):
        name = catalog.name(t.tracker)
        if tracker and name != tracker:
            continue
        if words and not all(w in t.name.casefold() for w in words):
            continue
        hnr = catalog.hnr(t, now)
        if status and hnr.status != status:
            continue
        rows.append(TorrentRow(t, name, hnr, palette.slot(name)))
    key = SORTS.get(sort, SORTS["added"])
    rows.sort(key=lambda r: key(r.torrent, r.hnr), reverse=desc)
    total = len(rows)
    pages = max((total + per_page - 1) // per_page, 1)
    page = min(max(page, 1), pages)
    return {
        "rows": rows[(page - 1) * per_page : page * per_page],
        "total": total,
        "size": sum(r.torrent.size for r in rows),
        "page": page,
        "pages": pages,
        "trackers": sorted({catalog.name(t.tracker) for t in analytics.active_torrents(session)}),
        "statuses": HNR_LABELS,
    }


# Watch list -----------------------------------------------------------------

REASON_ICONS = {
    "non enregistré sur le tracker": "x-circle",
    "tracker en erreur": "radio-tower",
    "erreur qBittorrent": "alert",
    "fichiers manquants": "file-x",
    "en pause": "pause",
    "bloqué sur les métadonnées": "clock",
    "téléchargement mort (aucun seeder)": "skull",
}


def watch_groups(session: Session, now: int | None = None) -> list[dict]:
    catalog = Catalog.load(session)
    groups: dict[str, dict] = {}
    for t, reason, hnr in analytics.watchlist(session, now, catalog):
        g = groups.setdefault(
            reason,
            {"reason": reason, "icon": REASON_ICONS.get(reason, "alert"), "items": [], "critical": False},
        )
        g["items"].append({"torrent": t, "tracker": catalog.name(t.tracker), "hnr": hnr})
        g["critical"] |= hnr.status == HnrStatus.AT_RISK or t.unregistered
    return sorted(groups.values(), key=lambda g: (not g["critical"], -len(g["items"])))


def journal(session: Session, limit: int = 200) -> list[ActionLog]:
    return list(session.scalars(select(ActionLog).order_by(ActionLog.ts.desc()).limit(limit)))
