"""Thin wrapper around qbittorrent-api returning plain dataclasses."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import qbittorrentapi
from qbittorrentapi import TrackerStatus

from seedkit.config import Settings

# Messages private trackers send when a torrent was deleted, trumped, etc.
UNREGISTERED_PATTERNS = re.compile(
    r"unregistered|not registered|torrent not found|not exist|unknown torrent"
    r"|trump|retitled|deleted|nuked|dupe",
    re.IGNORECASE,
)

TRACKER_ERROR_STATUSES = {
    TrackerStatus.NOT_WORKING,
    TrackerStatus.TRACKER_ERROR,
    TrackerStatus.UNREACHABLE,
}


@dataclass(frozen=True)
class TorrentData:
    hash: str
    name: str
    size: int
    uploaded: int
    downloaded: int
    ratio: float
    state: str
    category: str
    tags: str
    added_on: int
    completion_on: int
    seeding_time: int
    last_activity: int
    num_complete: int
    num_incomplete: int
    amount_left: int
    save_path: str
    content_path: str
    tracker: str
    tracker_status: int | None
    tracker_msg: str

    @property
    def unregistered(self) -> bool:
        return bool(self.tracker_msg) and bool(UNREGISTERED_PATTERNS.search(self.tracker_msg))


def make_client(settings: Settings) -> qbittorrentapi.Client:
    return qbittorrentapi.Client(
        host=settings.qbit_url,
        api_key=settings.qbit_api_key,
        username=settings.qbit_username,
        password=settings.qbit_password,
        VERIFY_WEBUI_CERTIFICATE=settings.qbit_verify_tls,
        REQUESTS_ARGS={"timeout": (10, 60)},
    )


def tracker_domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def main_tracker(trackers) -> tuple[str, int | None, str]:
    """Pick the tracker that represents a torrent: the first working one, else the first."""
    # DHT, PeX and LSD are listed as pseudo-trackers with URLs like "** [DHT] **".
    real = [t for t in trackers if not t["url"].startswith("**")]
    if not real:
        return "", None, ""
    working = next((t for t in real if t["status"] == TrackerStatus.WORKING), None)
    chosen = working or real[0]
    return tracker_domain(chosen["url"]), chosen["status"], chosen.get("msg") or ""


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


def fetch_torrents(client: qbittorrentapi.Client) -> list[TorrentData]:
    # include_trackers avoids one request per torrent (Web API >= 2.11.4).
    if version_tuple(client.app.web_api_version) >= (2, 11, 4):
        infos = client.torrents_info(include_trackers=True)
        trackers_of = lambda t: t.get("trackers", [])  # noqa: E731
    else:
        infos = client.torrents_info()
        trackers_of = lambda t: client.torrents_trackers(t.hash)  # noqa: E731

    result = []
    for t in infos:
        domain, status, msg = main_tracker(trackers_of(t))
        result.append(
            TorrentData(
                hash=t.hash,
                name=t.name,
                size=t.size,
                uploaded=t.uploaded,
                downloaded=t.downloaded,
                ratio=t.ratio,
                state=t.state,
                category=t.category,
                tags=t.tags,
                added_on=t.added_on,
                completion_on=max(t.completion_on, 0),
                seeding_time=t.get("seeding_time", 0),
                last_activity=t.last_activity,
                num_complete=t.num_complete,
                num_incomplete=t.num_incomplete,
                amount_left=t.amount_left,
                save_path=t.save_path,
                content_path=t.content_path,
                tracker=domain,
                tracker_status=status,
                tracker_msg=msg,
            )
        )
    return result
