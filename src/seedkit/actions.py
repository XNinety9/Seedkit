"""Non-destructive write actions on qBittorrent, all behind the SEEDKIT_ALLOW_ACTIONS lock."""

import time
from collections import defaultdict

from sqlalchemy.orm import Session

from seedkit import analytics
from seedkit.analytics import Catalog
from seedkit.config import Settings
from seedkit.db import ActionLog, Torrent
from seedkit.i18n import t as tr
from seedkit.rules import HnrStatus


class ActionsDisabled(Exception):
    pass


TAGS = {
    HnrStatus.SAFE: "hnr-ok",
    HnrStatus.PENDING: "hnr-en-cours",
    HnrStatus.AT_RISK: "hnr-danger",
}


def _check(settings: Settings) -> None:
    if not settings.seedkit_allow_actions:
        raise ActionsDisabled(tr("Les actions sont verrouillées (SEEDKIT_ALLOW_ACTIONS=false)."))


def _log(session: Session, action: str, torrents: list[Torrent], detail: str = "") -> None:
    now = int(time.time())
    session.add_all(ActionLog(ts=now, action=action, hash=t.hash, name=t.name, detail=detail) for t in torrents)
    session.commit()


def reannounce(client, session: Session, settings: Settings, hashes: list[str]) -> int:
    _check(settings)
    torrents = [t for h in hashes if (t := session.get(Torrent, h))]
    if torrents:
        client.torrents_reannounce(torrent_hashes=[t.hash for t in torrents])
        _log(session, "reannounce", torrents)
    return len(torrents)


def recheck(client, session: Session, settings: Settings, hashes: list[str]) -> int:
    _check(settings)
    torrents = [t for h in hashes if (t := session.get(Torrent, h))]
    if torrents:
        client.torrents_recheck(torrent_hashes=[t.hash for t in torrents])
        _log(session, "recheck", torrents)
    return len(torrents)


def desired_tags(t: Torrent, catalog: Catalog, prefix: str, now: int) -> set[str]:
    tags = {f"{prefix}{catalog.name(t.tracker)}"}
    if tag := TAGS.get(catalog.hnr(t, now).status):
        tags.add(prefix + tag)
    if t.unregistered:
        tags.add(prefix + "unregistered")
    return tags


def sync_tags(client, session: Session, settings: Settings, now: int | None = None) -> tuple[int, int]:
    """Make seedkit's prefixed tags match the current state. Returns (added, removed) tag assignments."""
    _check(settings)
    now = now or int(time.time())
    catalog = Catalog.load(session)
    prefix = settings.seedkit_tag_prefix
    to_add: dict[str, list[str]] = defaultdict(list)
    to_remove: dict[str, list[str]] = defaultdict(list)
    for t in analytics.active_torrents(session):
        current = {tag.strip() for tag in t.tags.split(",") if tag.strip().startswith(prefix)}
        wanted = desired_tags(t, catalog, prefix, now)
        for tag in wanted - current:
            to_add[tag].append(t.hash)
        for tag in current - wanted:
            to_remove[tag].append(t.hash)
    for tag, hashes in to_add.items():
        client.torrents_add_tags(tags=tag, torrent_hashes=hashes)
    for tag, hashes in to_remove.items():
        client.torrents_remove_tags(tags=tag, torrent_hashes=hashes)
    added, removed = sum(map(len, to_add.values())), sum(map(len, to_remove.values()))
    if added or removed:
        session.add(ActionLog(ts=now, action="tags", hash=None, detail=f"+{added} / -{removed}"))
        session.commit()
    return added, removed
