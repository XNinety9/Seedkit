"""Notifications to ntfy and/or Discord: H&R alerts and a daily summary."""

import json
import logging
import time

import requests
from sqlalchemy.orm import Session

from seedkit import analytics
from seedkit.config import Settings
from seedkit.db import KeyValue
from seedkit.i18n import t as tr
from seedkit.web.format import human_size, ratio

log = logging.getLogger(__name__)


def send(settings: Settings, title: str, message: str, urgent: bool = False) -> None:
    if settings.seedkit_ntfy_url:
        headers = {"Title": title.encode(), "Tags": "rotating_light" if urgent else "seedling"}
        if urgent:
            headers["Priority"] = "high"
        if settings.seedkit_ntfy_token:
            headers["Authorization"] = f"Bearer {settings.seedkit_ntfy_token}"
        requests.post(settings.seedkit_ntfy_url, data=message.encode(), headers=headers, timeout=15).raise_for_status()
    if settings.seedkit_discord_webhook:
        payload = {
            "embeds": [{"title": title, "description": message[:4000], "color": 0xE5484D if urgent else 0x30A46C}]
        }
        requests.post(settings.seedkit_discord_webhook, json=payload, timeout=15).raise_for_status()


def _get(session: Session, key: str, default):
    row = session.get(KeyValue, key)
    return json.loads(row.value) if row else default


def _put(session: Session, key: str, value, now: int) -> None:
    session.merge(KeyValue(key=key, value=json.dumps(value), ts=now))


def check_alerts(session: Session, settings: Settings, now: int | None = None) -> list[str]:
    """Notify torrents that newly need attention; each (torrent, problem) is notified once."""
    if not settings.notifications_enabled:
        return []
    now = now or int(time.time())
    current = {
        f"{tor.hash}|{reason}": f"{tor.name} — {tr(reason)}" for tor, reason, _ in analytics.watchlist(session, now)
    }
    already = set(_get(session, "alerts.sent", []))
    new = [label for key, label in current.items() if key not in already]
    if new:
        lines = "\n".join(f"• {label}" for label in new[:20])
        more = "\n" + tr("… et {n} autres", n=len(new) - 20) if len(new) > 20 else ""
        send(settings, tr("seedkit : {n} torrent(s) à surveiller", n=len(new)), lines + more, urgent=True)
    # Forget resolved problems so they are notified again if they come back.
    _put(session, "alerts.sent", sorted(current), now)
    session.commit()
    return new


def summary_text(session: Session, now: int | None = None) -> str:
    now = now or int(time.time())
    o = analytics.overview(session, now)
    top = analytics.ranking(session, "24h", now)[:3]
    watch = analytics.watchlist(session, now)
    lines = [
        tr("Upload 24 h : {day} · 7 j : {week}", day=human_size(o["uploaded_24h"]), week=human_size(o["uploaded_7d"])),
        tr("Ratio global : {ratio}", ratio=ratio(o["ratio"])),
        tr("{n} torrents · {size}", n=o["count"], size=human_size(o["size"])),
    ]
    if top:
        lines.append(tr("Meilleurs seeds (24 h) :"))
        lines += [f"  {i}. {r.torrent.name} — {human_size(r.uploaded)}" for i, r in enumerate(top, 1)]
    lines.append(tr("À surveiller : {n}", n=len(watch)) if watch else tr("Rien à signaler 👌"))
    return "\n".join(lines)


def maybe_send_summary(session: Session, settings: Settings, now: int | None = None) -> bool:
    if not settings.notifications_enabled or settings.seedkit_summary_hour is None:
        return False
    now = now or int(time.time())
    local = time.localtime(now)
    today = time.strftime("%Y-%m-%d", local)
    if local.tm_hour < settings.seedkit_summary_hour or _get(session, "summary.last", None) == today:
        return False
    send(settings, tr("seedkit : résumé du jour"), summary_text(session, now))
    _put(session, "summary.last", today, now)
    session.commit()
    return True
