"""Hit & Run status of torrents according to user-defined tracker rules."""

import time
from dataclasses import dataclass
from enum import StrEnum

from seedkit.db import Torrent, TrackerRule
from seedkit.qbit import TRACKER_ERROR_STATUSES


class HnrStatus(StrEnum):
    NO_RULE = "no_rule"  # untouchable until the user defines a rule
    DOWNLOADING = "downloading"
    PENDING = "pending"
    AT_RISK = "at_risk"
    SAFE = "safe"


HNR_LABELS = {
    HnrStatus.NO_RULE: "sans règle",
    HnrStatus.DOWNLOADING: "téléchargement",
    HnrStatus.PENDING: "H&R en cours",
    HnrStatus.AT_RISK: "H&R en danger",
    HnrStatus.SAFE: "H&R OK",
}


@dataclass(frozen=True)
class HnrResult:
    status: HnrStatus
    remaining_hours: float | None = None
    reason: str = ""
    progress: float | None = None  # 0..1 share of the H&R obligations already met


def is_problematic(t: Torrent, now: int) -> str:
    """Return why a torrent cannot seed properly, or an empty string."""
    if t.unregistered:
        return "non enregistré sur le tracker"
    if t.state in ("error", "missingFiles"):
        return "erreur qBittorrent" if t.state == "error" else "fichiers manquants"
    if t.tracker_status in TRACKER_ERROR_STATUSES:
        return "tracker en erreur"
    if t.state.startswith("stopped") or t.state.startswith("paused"):
        return "en pause"
    return ""


def hnr_status(t: Torrent, rule: TrackerRule | None, now: int | None = None) -> HnrResult:
    now = now or int(time.time())
    if rule is None:
        return HnrResult(HnrStatus.NO_RULE)
    if t.amount_left > 0:
        return HnrResult(HnrStatus.DOWNLOADING)

    shares = []
    remaining = None
    if rule.min_seed_hours:
        seeded = t.seeding_time / 3600
        shares.append(min(seeded / rule.min_seed_hours, 1.0))
        remaining = max(rule.min_seed_hours - seeded, 0)
    if rule.min_ratio:
        shares.append(min(t.ratio / rule.min_ratio, 1.0))

    progress = (max(shares) if rule.satisfy == "any" else min(shares)) if shares else 1.0
    if progress >= 1.0:
        return HnrResult(HnrStatus.SAFE, progress=1.0)

    problem = is_problematic(t, now)
    if problem:
        return HnrResult(HnrStatus.AT_RISK, remaining, problem, progress)
    return HnrResult(HnrStatus.PENDING, remaining, progress=progress)
