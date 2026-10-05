from seedkit.qbit import main_tracker
from tests.conftest import torrent


def test_main_tracker_skips_pseudo_trackers_and_prefers_working():
    trackers = [
        {"url": "** [DHT] **", "status": 2, "msg": ""},
        {"url": "https://down.example.org/announce", "status": 4, "msg": "timeout"},
        {"url": "https://Tracker.Example.org:443/abc/announce", "status": 2, "msg": ""},
    ]
    assert main_tracker(trackers) == ("tracker.example.org", 2, "")


def test_main_tracker_without_real_tracker():
    assert main_tracker([{"url": "** [PeX] **", "status": 2}]) == ("", None, "")


def test_unregistered_detection():
    assert torrent(tracker_msg="Unregistered torrent").unregistered
    assert torrent(tracker_msg="Torrent not found").unregistered
    assert not torrent(tracker_msg="").unregistered
    assert not torrent(tracker_msg="Connection timed out").unregistered
