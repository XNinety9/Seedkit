import pytest

from seedkit import smb
from tests.fakes import FakeFS


def test_parse_and_map_paths():
    mappings = smb.parse_path_map("/downloads=//box/dl; /downloads/seed=\\\\box\\seed")
    assert [m.qbit for m in mappings] == ["/downloads/seed", "/downloads"]  # longest first
    assert smb.map_path(mappings, "/downloads/seed/a/b.mkv") == "\\\\box\\seed\\a\\b.mkv"
    assert smb.map_path(mappings, "/downloads/other.mkv") == "\\\\box\\dl\\other.mkv"
    assert smb.map_path(mappings, "/downloadsX/x") is None
    with pytest.raises(ValueError):
        smb.parse_path_map("/no-separator")


def test_scan_finds_orphans_and_missing():
    mappings = smb.parse_path_map("/downloads=//box/dl")
    fs = FakeFS(
        {
            "\\\\box\\dl\\seed\\Movie.mkv": 100,
            "\\\\box\\dl\\seed\\Show\\E01.mkv": 50,
            "\\\\box\\dl\\seed\\Partial.mkv.!qB": 10,
            "\\\\box\\dl\\seed\\Old.Release\\a.mkv": 300,
            "\\\\box\\dl\\seed\\Old.Release\\b.nfo": 1,
            "\\\\box\\dl\\Thumbs.db": 5,
        }
    )
    torrents = [
        smb.TorrentFiles("h1", "Movie", ["/downloads/seed/Movie.mkv"]),
        smb.TorrentFiles("h2", "Show", ["/downloads/seed/Show/E01.mkv", "/downloads/seed/Show/E02.mkv"]),
        smb.TorrentFiles("h3", "Partial", ["/downloads/seed/Partial.mkv"]),
        smb.TorrentFiles("h4", "Elsewhere", ["/mnt/other/x.mkv"]),
    ]
    result = smb.scan(fs, mappings, torrents, ignore=["Thumbs.db"])
    assert [(o.path, o.size, o.files) for o in result.orphans] == [("\\\\box\\dl\\seed\\Old.Release", 301, 2)]
    assert [m["name"] for m in result.missing] == ["Show"]
    assert result.unmapped == ["Elsewhere"]
    assert result.known_size == 160
    assert smb.ScanResult.from_json(result.to_json()).orphan_size == result.orphan_size


def test_failed_walk_does_not_report_everything_missing():
    class Broken:
        def walk(self, root):
            raise OSError("access denied")
            yield

    result = smb.scan(Broken(), smb.parse_path_map("/d=//b/s"), [smb.TorrentFiles("h", "n", ["/d/x"])], [])
    assert result.errors and result.missing == []


def test_check_mapping():
    mappings = smb.parse_path_map("/downloads=//box/dl")
    fs = FakeFS({"\\\\box\\dl\\a.mkv": 1})
    ok = smb.check_mapping(fs, mappings, [smb.TorrentFiles("h", "a", ["/downloads/a.mkv"])])
    assert ok["ok"] and ok["checked"][0]["found"]
    bad = smb.check_mapping(fs, mappings, [smb.TorrentFiles("h", "b", ["/downloads/b.mkv"])])
    assert not bad["ok"]


def test_stray_file_in_known_folder_is_its_own_group():
    mappings = smb.parse_path_map("/d=//b/s")
    fs = FakeFS({"\\\\b\\s\\seed\\Known.mkv": 1, "\\\\b\\s\\seed\\Stray.mkv": 7})
    result = smb.scan(fs, mappings, [smb.TorrentFiles("h", "k", ["/d/seed/Known.mkv"])], [])
    assert [(o.path, o.size) for o in result.orphans] == [("\\\\b\\s\\seed\\Stray.mkv", 7)]
