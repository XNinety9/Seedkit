"""Automatic cleanup: rules marked `auto: true` delete their candidates after a grace period.

A torrent must keep matching the same automatic rule at every collection for the whole grace period; if it
stops matching even once, its countdown starts again. A notification is sent when the countdown starts.
Nothing is deleted unless SEEDKIT_ALLOW_DELETE=true.
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from seedkit import cleanup, notify
from seedkit.config import Settings
from seedkit.db import AutoPending
from seedkit.engine import Evaluation
from seedkit.i18n import t as tr
from seedkit.web.format import human_duration, human_size

log = logging.getLogger(__name__)


@dataclass
class Pending:
    hash: str
    name: str
    size: int
    rule: str
    first_seen: int
    due: int  # timestamp when the grace period ends

    def progress(self, now: int) -> float:
        span = max(self.due - self.first_seen, 1)
        return min(max((now - self.first_seen) / span, 0.0), 1.0)


def track(session: Session, evaluation: Evaluation, now: int) -> list[Pending]:
    """Update the countdowns from a fresh evaluation and return every pending torrent."""
    auto = {m.torrent.hash: m for m in evaluation.candidates if m.rule.auto}
    rows = {r.hash: r for r in session.scalars(select(AutoPending))}
    for h, row in rows.items():
        if h not in auto or auto[h].rule.name != row.rule:
            session.delete(row)  # stopped matching: the countdown starts over
    for h, m in auto.items():
        row = rows.get(h)
        if row is None or row.rule != m.rule.name:
            session.add(AutoPending(hash=h, rule=m.rule.name, first_seen=now, last_seen=now))
        else:
            row.last_seen = now
    session.commit()
    return [
        Pending(h, m.torrent.name, m.torrent.size, m.rule.name, first, first + m.rule.grace)
        for h, m in auto.items()
        for first in [session.get(AutoPending, h).first_seen]
    ]


def run(client, session: Session, settings: Settings, evaluation: Evaluation, now: int | None = None) -> list:
    """Track countdowns, notify new ones, delete the due ones when allowed. Returns the deleted torrents."""
    now = now or int(time.time())
    pending = track(session, evaluation, now)

    fresh = list(session.scalars(select(AutoPending).where(AutoPending.notified.is_(False))))
    if fresh and settings.notifications_enabled:
        by_hash = {p.hash: p for p in pending}
        lines = [
            tr(
                "• {name} ({size}) — « {rule} », dans {delay}",
                name=by_hash[r.hash].name,
                size=human_size(by_hash[r.hash].size),
                rule=r.rule,
                delay=human_duration(by_hash[r.hash].due - now),
            )
            for r in fresh
            if r.hash in by_hash
        ]
        try:
            notify.send(
                settings,
                tr("seedkit : {n} torrent(s) programmé(s) pour suppression", n=len(lines)),
                "\n".join(lines[:20]),
            )
        except Exception:
            log.exception("Notification failed")
    for r in fresh:
        r.notified = True
    session.commit()

    due = [p for p in pending if p.due <= now]
    if not due or not settings.seedkit_allow_delete:
        return []
    deleted = []
    rules = {r.rule.name: r.rule for r in evaluation.results}
    for rule_name in {p.rule for p in due}:
        hashes = [p.hash for p in due if p.rule == rule_name]
        rule = rules[rule_name]
        deleted += cleanup.delete(client, session, settings, hashes, rule.delete_files, now, rule=rule_name)
    session.execute(delete(AutoPending).where(AutoPending.hash.in_([t.hash for t in deleted])))
    session.commit()
    if deleted:
        log.info("Auto-cleanup deleted %d torrent(s)", len(deleted))
    return deleted


def pending(session: Session, evaluation: Evaluation) -> list[Pending]:
    """Current countdowns, for display (does not modify anything)."""
    rows = {r.hash: r for r in session.scalars(select(AutoPending))}
    result = []
    for m in evaluation.candidates:
        if m.rule.auto and (row := rows.get(m.torrent.hash)) and row.rule == m.rule.name:
            result.append(
                Pending(
                    m.torrent.hash,
                    m.torrent.name,
                    m.torrent.size,
                    row.rule,
                    row.first_seen,
                    row.first_seen + m.rule.grace,
                )
            )
    return sorted(result, key=lambda p: p.due)
