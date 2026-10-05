"""Duplicate detection.

Torrents pointing to the same data (same content path) are one *unit*: that is cross-seeding, which costs no
space and is never a duplicate. Duplicates are groups of several units:

- same_files: identical file lists (names + sizes) stored twice;
- same_title: the same movie / episode / season pack in several releases (another quality, language…);
- episode_in_pack: single episodes already contained in a season pack of the same or better quality
  (both qualities must be known, or both unknown).
"""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from seedkit import releases
from seedkit.db import FileSignature, Torrent


@dataclass
class Unit:
    content_path: str
    torrents: list[Torrent]
    release: releases.Release

    @property
    def label(self) -> str:
        return self.torrents[0].name

    @property
    def size(self) -> int:
        return max(t.size for t in self.torrents)

    def metric(self, strategy: str, efficiency: dict[str, float]) -> tuple:
        """Bigger is better for every strategy."""
        ts = self.torrents
        return {
            "highest_quality": (self.release.resolution or 0, self.size),
            "best_ratio": (max(t.ratio for t in ts),),
            "best_efficiency": (max(efficiency.get(t.hash, 0.0) for t in ts),),
            "most_seeders": (max(t.num_complete for t in ts),),
            "oldest": (-min(t.added_on for t in ts),),
            "newest": (max(t.added_on for t in ts),),
            "largest": (self.size,),
            "smallest": (-self.size,),
        }[strategy]


@dataclass
class Group:
    kind: str
    key: tuple
    units: list[Unit]
    packs: list[Unit] = field(default_factory=list)  # episode_in_pack only

    def kept(self, strategy: str, efficiency: dict[str, float]) -> list[Unit]:
        if self.kind == "episode_in_pack":
            return self.packs
        best = max(self.units, key=lambda u: (u.metric(strategy, efficiency), -min(t.added_on for t in u.torrents)))
        return [best]

    def redundant(self, strategy: str, efficiency: dict[str, float]) -> list[Unit]:
        kept = {id(u) for u in self.kept(strategy, efficiency)}
        return [u for u in self.units if id(u) not in kept]


def _units(torrents: list[Torrent]) -> list[Unit]:
    by_path: dict[str, list[Torrent]] = defaultdict(list)
    for t in torrents:
        by_path[(t.content_path or t.hash).rstrip("/").casefold()].append(t)
    return [Unit(path, ts, releases.parse(ts[0].name)) for path, ts in by_path.items()]


def find(session: Session, torrents: list[Torrent]) -> dict[str, list[Group]]:
    complete = [t for t in torrents if t.amount_left == 0 and t.removed_at is None]
    units = _units(complete)
    groups: dict[str, list[Group]] = {"same_files": [], "same_title": [], "episode_in_pack": []}

    signatures = dict(
        session.execute(
            select(FileSignature.hash, FileSignature.signature).where(
                FileSignature.hash.in_([t.hash for t in complete])
            )
        ).all()
    )
    by_signature: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
        sigs = {signatures[t.hash] for t in u.torrents if t.hash in signatures}
        if len(sigs) == 1:
            by_signature[sigs.pop()].append(u)
    groups["same_files"] = [Group("same_files", (sig,), us) for sig, us in by_signature.items() if len(us) > 1]

    by_work: dict[tuple, list[Unit]] = defaultdict(list)
    for u in units:
        if key := releases.same_work_key(u.release):
            by_work[key].append(u)
    groups["same_title"] = [Group("same_title", key, us) for key, us in by_work.items() if len(us) > 1]

    packs: dict[tuple, list[Unit]] = defaultdict(list)
    episodes: dict[tuple, list[Unit]] = defaultdict(list)
    for u in units:
        r = u.release
        if not r.title or r.season is None:
            continue
        (packs if r.is_pack else episodes)[(r.title, r.season)].append(u)
    for key, season_packs in packs.items():
        covered = [
            e
            for e in episodes.get(key, [])
            if any(_covers(p.release.resolution, e.release.resolution) for p in season_packs)
        ]
        if covered:
            groups["episode_in_pack"].append(Group("episode_in_pack", key, season_packs + covered, season_packs))
    return groups


def _covers(pack_resolution: int | None, episode_resolution: int | None) -> bool:
    """A pack covers an episode of the same or lower quality. When only one quality is known we cannot tell,
    so the episode is kept (a deletion must never rely on a guess)."""
    if pack_resolution is None or episode_resolution is None:
        return pack_resolution is None and episode_resolution is None
    return pack_resolution >= episode_resolution
