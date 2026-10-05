"""Export the .torrent files of every torrent to a local folder (read-only on qBittorrent)."""

import re
from pathlib import Path

UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def filename(name: str, torrent_hash: str) -> str:
    safe = UNSAFE.sub("_", name).strip(" .")[:150] or "torrent"
    return f"{safe} [{torrent_hash[:8]}].torrent"


def export_torrents(client, directory: str | Path) -> tuple[int, int]:
    """Write missing .torrent files; existing ones are kept. Returns (written, skipped)."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for t in client.torrents_info():
        path = target / filename(t.name, t.hash)
        if path.exists():
            skipped += 1
            continue
        path.write_bytes(client.torrents_export(torrent_hash=t.hash))
        written += 1
    return written, skipped
