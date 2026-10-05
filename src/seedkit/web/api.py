"""Read-only JSON API, consumed by the TUI."""

from fastapi import APIRouter, HTTPException, Request

from seedkit import analytics, views
from seedkit.i18n import t as tr
from seedkit.rules import HNR_LABELS

router = APIRouter(prefix="/api")


def _db(request: Request):
    return request.app.state.sessions()


def _torrent(t, tracker: str, hnr=None) -> dict:
    data = {
        "hash": t.hash,
        "name": t.name,
        "tracker": tracker,
        "size": t.size,
        "uploaded": t.uploaded,
        "ratio": t.ratio,
        "state": t.state,
        "seeding_time": t.seeding_time,
        "seeders": t.num_complete,
        "leechers": t.num_incomplete,
        "added_on": t.added_on,
    }
    if hnr is not None:
        data["hnr"] = {
            "status": hnr.status.value,
            "label": tr(HNR_LABELS[hnr.status]),
            "progress": hnr.progress,
            "remaining_hours": hnr.remaining_hours,
        }
    return data


@router.get("/status")
def status(request: Request):
    service = request.app.state.service
    settings = request.app.state.settings
    return {
        "last_run": service.last_run,
        "last_error": service.last_error,
        "qbit_url": settings.qbit_url,
        "lang": request.state.lang,
        "locks": {"actions": not settings.seedkit_allow_actions, "delete": not settings.seedkit_allow_delete},
    }


@router.get("/dashboard")
def dashboard(request: Request, window: str | None = None):
    if window is not None and window not in analytics.WINDOWS:
        raise HTTPException(400, tr("fenêtre inconnue"))
    with _db(request) as session:
        d = views.dashboard(session, window)
    ranked = lambda rows: [  # noqa: E731
        _torrent(r.torrent, r.tracker) | {"window_uploaded": r.uploaded, "efficiency": r.efficiency} for r in rows
    ]
    return {
        "window": d["window"],
        "history_days": d["history_days"],
        "overview": d["overview"],
        "status_totals": {k.value: v for k, v in d["status_totals"].items()},
        "trackers": [
            {
                "name": s.name,
                "domains": sorted(s.domains),
                "count": s.count,
                "size": s.size,
                "uploaded": s.uploaded,
                "uploaded_7d": s.uploaded_window,
                "ratio": s.ratio,
                "statuses": {k.value: v for k, v in s.statuses.items()},
                "slot": d["palette"].slot(s.name),
            }
            for s in d["trackers"]
        ],
        "top": ranked(d["top"]),
        "flop": ranked(d["flop"]),
        "watch_count": len(d["watch"]),
        "charts": {k: d["charts"][k] for k in ("daily", "hourly", "daily_total", "disk", "age")},
    }


@router.get("/torrents")
def torrents(
    request: Request,
    q: str = "",
    tracker: str = "",
    status: str = "",
    sort: str = "added",
    desc: bool = True,
    page: int = 1,
    per_page: int = 200,
):
    with _db(request) as session:
        d = views.torrents(session, q, tracker, status, sort, desc, page, min(per_page, 1000))
    return {
        "total": d["total"],
        "size": d["size"],
        "page": d["page"],
        "pages": d["pages"],
        "trackers": d["trackers"],
        "rows": [_torrent(r.torrent, r.tracker, r.hnr) for r in d["rows"]],
    }


@router.get("/watch")
def watch(request: Request):
    with _db(request) as session:
        groups = views.watch_groups(session)
    return [
        {
            "reason": tr(g["reason"]),
            "critical": g["critical"],
            "items": [_torrent(i["torrent"], i["tracker"], i["hnr"]) for i in g["items"]],
        }
        for g in groups
    ]
