"""Lightweight scene/P2P release name parser: just enough to group the same title across releases."""

import re
import unicodedata
from dataclasses import dataclass

EPISODE = re.compile(r"^s(\d{1,2})e(\d{1,3})(?:-?e?\d{1,3})*$", re.I)
SEASON = re.compile(r"^s(\d{1,2})$", re.I)
SEASON_WORD = re.compile(r"^(saison|season)$", re.I)
YEAR = re.compile(r"^(19[3-9]\d|20\d\d)$")
RESOLUTIONS = {
    "2160p": 2160,
    "4k": 2160,
    "uhd": 2160,
    "1080p": 1080,
    "1080i": 1080,
    "720p": 720,
    "576p": 576,
    "480p": 480,
}
# Tokens that end the title even when no year/season/resolution comes first.
STOP = {
    "multi", "multi2", "vff", "vfq", "vfi", "vf2", "vf", "vostfr", "french", "truefrench", "subfrench", "english",
    "webrip", "web", "webdl", "web-dl", "bluray", "bdrip", "hdtv", "dvdrip", "hdrip", "remux", "complete", "integrale",
    "repack", "proper", "x264", "x265", "h264", "h265", "hevc", "custom", "extended", "hybrid", "dvdr",
}  # fmt: skip


@dataclass(frozen=True)
class Release:
    title: str  # normalized: lowercase, accents removed, single spaces
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    resolution: int | None = None  # vertical resolution, e.g. 1080

    @property
    def is_pack(self) -> bool:
        return self.season is not None and self.episode is None

    @property
    def is_episode(self) -> bool:
        return self.episode is not None

    @property
    def identifiable(self) -> bool:
        """Enough information to compare with other releases without false positives."""
        return bool(self.title) and (self.year is not None or self.season is not None)


def _normalize(words: list[str]) -> str:
    text = " ".join(words).lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def _tokens(name: str) -> list[str]:
    name = re.sub(r"\.(mkv|mp4|avi|m4v|ts|iso)$", "", name, flags=re.I)
    name = re.sub(r"^\[[^\]]*\]\s*", "", name)  # leading [group] tag
    return [t for t in re.split(r"[\s._\-\[\]()]+", name) if t]


def parse(name: str) -> Release:
    tokens = _tokens(name)
    title_words: list[str] = []
    year = season = episode = resolution = None
    title_done = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        if m := EPISODE.match(tok):
            season, episode = int(m.group(1)), int(m.group(2))
            title_done = True
        elif m := SEASON.match(tok):
            season = int(m.group(1))
            title_done = True
        elif SEASON_WORD.match(tok) and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            season = int(tokens[i + 1])
            title_done = True
            i += 1
        elif YEAR.match(tok) and title_words:
            # A year inside the title ("2001 A Space Odyssey") only counts once a title exists.
            year = year or int(tok)
            title_done = True
        elif low in RESOLUTIONS:
            resolution = resolution or RESOLUTIONS[low]
            title_done = True
        elif low in STOP:
            title_done = True
        elif not title_done:
            title_words.append(tok)
        i += 1
    return Release(_normalize(title_words), year, season, episode, resolution)


def same_work_key(r: Release) -> tuple | None:
    """Key shared by every release of the same movie / episode / season pack, whatever the quality."""
    if not r.identifiable:
        return None
    if r.season is not None:
        # Series: the year is often omitted ("Show.2023.S03E07" vs "Show.S03E07"), so it is ignored.
        return ("series", r.title, r.season, r.episode)
    return ("movie", r.title, r.year)
