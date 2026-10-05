from seedkit.collector import store
from seedkit.db import Torrent, TrackerRule
from seedkit.rules import HnrStatus, hnr_status
from tests.conftest import DAY, NOW, torrent


def stored(session, **overrides) -> Torrent:
    data = torrent(**overrides)
    store(session, [data], now=NOW)
    return session.get(Torrent, data.hash)


def test_no_rule_means_untouchable(session):
    assert hnr_status(stored(session), None, NOW).status == HnrStatus.NO_RULE


def test_pending_then_safe_on_seed_time(session):
    rule = TrackerRule(tracker="x", min_seed_hours=72, min_ratio=None, satisfy="any")
    pending = hnr_status(stored(session, seeding_time=DAY), rule, NOW)
    assert pending.status == HnrStatus.PENDING
    assert pending.remaining_hours == 48
    assert hnr_status(stored(session, seeding_time=3 * DAY), rule, NOW).status == HnrStatus.SAFE


def test_any_vs_all(session):
    t = stored(session, seeding_time=DAY, ratio=1.5)
    any_rule = TrackerRule(tracker="x", min_seed_hours=72, min_ratio=1.0, satisfy="any")
    all_rule = TrackerRule(tracker="x", min_seed_hours=72, min_ratio=1.0, satisfy="all")
    assert hnr_status(t, any_rule, NOW).status == HnrStatus.SAFE
    assert hnr_status(t, all_rule, NOW).status == HnrStatus.PENDING


def test_at_risk_when_not_seeding(session):
    rule = TrackerRule(tracker="x", min_seed_hours=72, min_ratio=None, satisfy="any")
    for overrides, reason in [
        ({"state": "stoppedUP"}, "en pause"),
        ({"tracker_msg": "unregistered torrent"}, "non enregistré sur le tracker"),
        ({"tracker_status": 4}, "tracker en erreur"),
        ({"state": "missingFiles"}, "fichiers manquants"),
    ]:
        result = hnr_status(stored(session, seeding_time=DAY, **overrides), rule, NOW)
        assert (result.status, result.reason) == (HnrStatus.AT_RISK, reason)


def test_downloading(session):
    rule = TrackerRule(tracker="x", min_seed_hours=72, min_ratio=None, satisfy="any")
    assert hnr_status(stored(session, amount_left=1), rule, NOW).status == HnrStatus.DOWNLOADING
