"""Deletion of torrents, guarded by safety checks shared with the rules engine.

A torrent can only ever be deleted when its tracker has a rule and its H&R obligations are met, or when the
tracker no longer knows it (unregistered). Torrents of trackers without rules are untouchable.
"""

import time

from sqlalchemy.orm import Session

from seedkit import analytics
from seedkit.analytics import Catalog
from seedkit.config import Settings
from seedkit.db import ActionLog, Torrent
from seedkit.i18n import t as tr
from seedkit.rules import HnrStatus

# ActionLog.detail is "<translatable key>|<rule name>"; the journal translates the first part.
DETAIL_WITH_DATA = "torrent + données"
DETAIL_TORRENT_ONLY = "torrent seul"
DETAIL_SHARED = "torrent seul (données partagées avec un autre torrent)"


class DeletionDisabled(Exception):
    pass


def eligible(t: Torrent, catalog: Catalog, now: int) -> tuple[bool, str]:
    """Hard safety gate shared by the rules engine and the deletion. Returns (ok, why)."""
    if catalog.rule(t) is None:
        return False, "tracker sans règle (intouchable)"
    hnr = catalog.hnr(t, now)
    if hnr.status == HnrStatus.DOWNLOADING:
        return False, "en téléchargement"
    if t.unregistered:
        return True, ""
    if hnr.status != HnrStatus.SAFE:
        return False, "H&R pas encore rempli"
    return True, ""


def _path(t: Torrent) -> str:
    return (t.content_path or t.hash).rstrip("/").casefold()


def delete(
    client,
    session: Session,
    settings: Settings,
    hashes: list[str],
    delete_files: bool,
    now: int | None = None,
    rule: str = "",
) -> list[Torrent]:
    """Delete the given torrents after re-checking each of them. Returns the deleted torrents.

    Data shared with a torrent that is kept (cross-seeding) is never deleted: those torrents are removed
    without their files.
    """
    if not settings.seedkit_allow_delete:
        raise DeletionDisabled(tr("La suppression est verrouillée (SEEDKIT_ALLOW_DELETE=false)."))
    now = now or int(time.time())
    catalog = Catalog.load(session)
    selected: list[Torrent] = []
    for h in dict.fromkeys(hashes):
        torrent = session.get(Torrent, h)
        if torrent is None or torrent.removed_at is not None:
            continue
        if eligible(torrent, catalog, now)[0]:
            selected.append(torrent)
    if not selected:
        return []

    leaving = {x.hash for x in selected}
    kept_paths = {_path(x) for x in analytics.active_torrents(session) if x.hash not in leaving}
    with_data = [x for x in selected if delete_files and _path(x) not in kept_paths]
    without_data = [x for x in selected if x not in with_data]
    if with_data:
        client.torrents_delete(delete_files=True, torrent_hashes=[x.hash for x in with_data])
    if without_data:
        client.torrents_delete(delete_files=False, torrent_hashes=[x.hash for x in without_data])

    for x in selected:
        if x in with_data:
            detail = DETAIL_WITH_DATA
        elif delete_files:
            detail = DETAIL_SHARED
        else:
            detail = DETAIL_TORRENT_ONLY
        x.removed_at = now
        session.add(ActionLog(ts=now, action="delete", hash=x.hash, name=x.name, detail=f"{detail}|{rule}"))
    session.commit()
    return selected
