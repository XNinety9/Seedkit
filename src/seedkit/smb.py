"""Read-only comparison between qBittorrent's view of the files and what is really on the SMB share.

Nothing here ever writes to or deletes from the share: orphans are only reported.
"""

import fnmatch
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Protocol

from seedkit.config import Settings

PARTIAL_SUFFIX = ".!qB"  # qBittorrent's optional extension for incomplete files


@dataclass(frozen=True)
class Mapping:
    qbit: str  # e.g. /data/torrents
    smb: str  # e.g. \\seedbox\torrents

    def to_smb(self, qbit_path: str) -> str | None:
        if qbit_path == self.qbit or qbit_path.startswith(self.qbit.rstrip("/") + "/"):
            rest = qbit_path[len(self.qbit.rstrip("/")) :].strip("/")
            return self.smb.rstrip("\\") + ("\\" + rest.replace("/", "\\") if rest else "")
        return None


def parse_path_map(value: str) -> list[Mapping]:
    """Parse "qbit=smb;qbit2=smb2", SMB paths given as //server/share/dir or \\\\server\\share\\dir."""
    mappings = []
    for part in filter(None, (p.strip() for p in value.split(";"))):
        qbit, sep, smb = part.partition("=")
        if not sep or not qbit.strip() or not smb.strip():
            raise ValueError(f"correspondance invalide : {part!r} (attendu : /chemin/qbit=//serveur/partage)")
        smb = "\\\\" + smb.strip().replace("/", "\\").lstrip("\\")
        mappings.append(Mapping(qbit.strip().rstrip("/") or "/", smb.rstrip("\\")))
    # Longest prefix first so nested mappings win.
    return sorted(mappings, key=lambda m: len(m.qbit), reverse=True)


def map_path(mappings: list[Mapping], qbit_path: str) -> str | None:
    return next((p for m in mappings if (p := m.to_smb(qbit_path)) is not None), None)


class FileSystem(Protocol):
    def walk(self, root: str) -> Iterator[tuple[str, int]]: ...

    def exists(self, path: str) -> bool: ...


class SmbFileSystem:
    def __init__(self, settings: Settings):
        import smbclient

        self._smb = smbclient
        smbclient.register_session(
            settings.smb_server,
            username=settings.smb_username,
            password=settings.smb_password,
            port=settings.smb_port,
        )

    def exists(self, path: str) -> bool:
        try:
            self._smb.stat(path)
            return True
        except OSError:
            return False

    def walk(self, root: str) -> Iterator[tuple[str, int]]:
        stack = [root]
        while stack:
            current = stack.pop()
            for entry in self._smb.scandir(current):
                if entry.is_dir():
                    stack.append(entry.path)
                elif entry.is_file():
                    yield entry.path, entry.stat().st_size


@dataclass
class TorrentFiles:
    hash: str
    name: str
    paths: list[str]  # absolute qBittorrent paths of the files that should exist


@dataclass
class OrphanGroup:
    path: str  # first-level entry under the mapped root
    size: int
    files: int


@dataclass
class ScanResult:
    ts: int
    roots: list[str]
    scanned_files: int = 0
    scanned_size: int = 0
    known_size: int = 0
    orphans: list[OrphanGroup] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)  # {hash, name, path}
    unmapped: list[str] = field(default_factory=list)  # torrent names outside every mapping
    errors: list[str] = field(default_factory=list)

    @property
    def orphan_size(self) -> int:
        return sum(o.size for o in self.orphans)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ScanResult":
        return cls(**(data | {"orphans": [OrphanGroup(**o) for o in data.get("orphans", [])]}))


def _key(path: str) -> str:
    return path.replace("/", "\\").casefold()


def _ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(part, pat) for part in path.split("\\") for pat in patterns)


def _orphan_root(path: str, root: str, known_dirs: set[str]) -> str:
    parts = path[len(root) :].strip("\\").split("\\")
    current = root.rstrip("\\")
    for part in parts:
        current = f"{current}\\{part}"
        if _key(current) not in known_dirs:
            return current
    return path


def scan(fs: FileSystem, mappings: list[Mapping], torrents: list[TorrentFiles], ignore: list[str]) -> ScanResult:
    result = ScanResult(ts=int(time.time()), roots=[m.smb for m in mappings])
    expected: dict[str, tuple[str, str]] = {}  # key → (hash, name)
    for t in torrents:
        mapped_any = False
        for p in t.paths:
            if (smb := map_path(mappings, p)) is not None:
                expected[_key(smb)] = (t.hash, t.name)
                mapped_any = True
        if t.paths and not mapped_any:
            result.unmapped.append(t.name)

    # Directories holding at least one known file: an orphan is grouped under the highest
    # directory that holds none, so a leftover release folder shows up as one entry.
    known_dirs = {k.rsplit("\\", i)[0] for k in expected for i in range(1, k.count("\\"))}

    seen: set[str] = set()
    groups: dict[str, OrphanGroup] = {}
    for m in mappings:
        try:
            for path, size in fs.walk(m.smb):
                if _ignored(path, ignore):
                    continue
                result.scanned_files += 1
                result.scanned_size += size
                key = _key(path.removesuffix(PARTIAL_SUFFIX))
                if key in expected:
                    seen.add(key)
                    result.known_size += size
                    continue
                group_path = _orphan_root(path, m.smb, known_dirs)
                g = groups.setdefault(group_path, OrphanGroup(group_path, 0, 0))
                g.size += size
                g.files += 1
        except OSError as exc:
            result.errors.append(f"{m.smb} : {exc}")

    if not result.errors:  # a failed walk would flag everything below it as missing
        for key, (h, name) in expected.items():
            if key not in seen:
                result.missing.append({"hash": h, "name": name, "path": key})
    result.orphans = sorted(groups.values(), key=lambda g: g.size, reverse=True)
    return result


def check_mapping(fs: FileSystem, mappings: list[Mapping], torrents: list[TorrentFiles], sample: int = 8) -> dict:
    """Check that a few torrent files are found through the mapping, before trusting a full scan."""
    paths = [p for t in torrents for p in t.paths[:1]]
    step = max(len(paths) // sample, 1)
    checked = []
    for p in paths[::step][:sample]:
        smb = map_path(mappings, p)
        checked.append({"qbit": p, "smb": smb, "found": bool(smb) and fs.exists(smb)})
    return {"checked": checked, "ok": bool(checked) and all(c["found"] for c in checked)}


def torrent_files(client) -> list[TorrentFiles]:
    """Files each torrent should have on disk (completed, wanted files only). Read-only."""
    from seedkit.qbit import version_tuple

    result = []
    if version_tuple(client.app.web_api_version) >= (2, 11, 7):
        infos = [(t, t.get("files", [])) for t in client.torrents_info(include_files=True)]
    else:
        infos = [(t, client.torrents_files(t.hash)) for t in client.torrents_info()]
    for t, files in infos:
        base = t.save_path.rstrip("/")
        paths = [f"{base}/{f['name']}" for f in files if f.get("priority", 1) != 0 and f.get("progress", 1) >= 1]
        result.append(TorrentFiles(t.hash, t.name, paths))
    return result
