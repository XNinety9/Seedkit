"""Cleanup candidates (preview) and their deletion, guarded by several safety checks.

A torrent can only ever be a candidate when its tracker has a rule and its H&R obligations are met,
or when the tracker no longer knows it (unregistered). Torrents of trackers without rules are untouchable.
"""

import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from seedkit import analytics
from seedkit.analytics import Catalog
from seedkit.config import Settings
from seedkit.db import ActionLog, Torrent
from seedkit.i18n import t as tr
from seedkit.rules import HnrStatus


class DeletionDisabled(Exception):
    pass


@dataclass
class Criteria:
    trackers: list[str] = field(default_factory=list)  # tracker names; empty means all
    window: str = "30d"
    max_efficiency: float | None = 0.01  # Go uploaded per Go stored per day, over the window
    min_seed_days: float = 0
    unregistered: bool = True  # always propose torrents the tracker deleted


@dataclass
class Candidate:
    torrent: Torrent
    tracker: str
    reason: str
    uploaded: int
    efficiency: float


@dataclass
class Preview:
    candidates: list[Candidate]
    excluded: dict[str, int]  # why other torrents were not proposed, with counts

    @property
    def size(self) -> int:
        return sum(c.torrent.size for c in self.candidates)


def eligible(t: Torrent, catalog: Catalog, now: int) -> tuple[bool, str]:
    """Hard safety gate shared by the preview and the deletion. Returns (ok, why)."""
    if catalog.rule(t) is None:
        return False, "tracker sans règle (intouchable)"
    hnr = catalog.hnr(t, now)
    if hnr.status == HnrStatus.DOWNLOADING:
        return False, "en téléchargement"
    if t.unregistered:
        return True, "non enregistré sur le tracker"
    if hnr.status != HnrStatus.SAFE:
        return False, "H&R pas encore rempli"
    return True, ""


def preview(session: Session, criteria: Criteria, now: int | None = None) -> Preview:
    now = now or int(time.time())
    catalog = Catalog.load(session)
    torrents = analytics.active_torrents(session)
    uploads = analytics.window_uploads(session, torrents, analytics.WINDOWS[criteria.window], now)
    candidates, excluded = [], {}

    def exclude(why: str) -> None:
        excluded[why] = excluded.get(why, 0) + 1

    for t in torrents:
        name = catalog.name(t.tracker)
        if criteria.trackers and name not in criteria.trackers:
            continue
        ok, why = eligible(t, catalog, now)
        if not ok:
            exclude(why)
            continue
        uploaded, days = uploads[t.hash]
        efficiency = uploaded / t.size / days if t.size and days else 0.0
        if t.unregistered:
            if criteria.unregistered:
                candidates.append(Candidate(t, name, why, uploaded, efficiency))
            else:
                exclude("non enregistré (non inclus)")
            continue
        if t.seeding_time < criteria.min_seed_days * analytics.DAY:
            exclude("seedé depuis trop peu de temps")
            continue
        if criteria.max_efficiency is not None and efficiency > criteria.max_efficiency:
            exclude("assez rentable")
            continue
        candidates.append(Candidate(t, name, "peu rentable", uploaded, efficiency))

    candidates.sort(key=lambda c: (not c.torrent.unregistered, c.efficiency, -c.torrent.size))
    return Preview(candidates, dict(sorted(excluded.items(), key=lambda kv: -kv[1])))


def delete(
    client,
    session: Session,
    settings: Settings,
    hashes: list[str],
    delete_files: bool,
    now: int | None = None,
) -> list[Torrent]:
    """Delete the given torrents after re-checking each of them. Returns the deleted torrents."""
    if not settings.seedkit_allow_delete:
        raise DeletionDisabled(tr("La suppression est verrouillée (SEEDKIT_ALLOW_DELETE=false)."))
    now = now or int(time.time())
    catalog = Catalog.load(session)
    selected = []
    for h in dict.fromkeys(hashes):
        t = session.get(Torrent, h)
        if t is None or t.removed_at is not None:
            continue
        ok, _ = eligible(t, catalog, now)
        if ok:
            selected.append(t)
    if not selected:
        return []

    client.torrents_delete(delete_files=delete_files, torrent_hashes=[t.hash for t in selected])
    detail = "torrent + données" if delete_files else "torrent seul"
    for t in selected:
        t.removed_at = now
        session.add(ActionLog(ts=now, action="delete", hash=t.hash, name=t.name, detail=detail))
    session.commit()
    return selected
