"""Cleanup rules engine: evaluates a RuleSet against the current torrents and explains every match."""

import re
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from seedkit import analytics, duplicates
from seedkit.analytics import Catalog
from seedkit.cleanup import eligible
from seedkit.collector import free_space
from seedkit.db import Torrent
from seedkit.i18n import t as tr
from seedkit.ruleset import DEFAULT_KEEP, All, Any, Leaf, Not, Rule, RuleSet
from seedkit.web.format import human_duration, human_size, number

FIELD_LABELS = {
    "tracker": "tracker",
    "category": "catégorie",
    "tag": "tag",
    "name": "nom",
    "state": "état",
    "hnr": "statut H&R",
    "unregistered": "non enregistré",
    "duplicate": "doublon",
    "size": "taille",
    "uploaded": "uploadé",
    "upload_24h": "upload 24 h",
    "upload_7d": "upload 7 j",
    "upload_30d": "upload 30 j",
    "ratio": "ratio",
    "efficiency_24h": "efficacité 24 h",
    "efficiency_7d": "efficacité 7 j",
    "efficiency_30d": "efficacité 30 j",
    "efficiency_all": "efficacité",
    "seed_time": "temps de seed",
    "age": "âge",
    "inactive": "inactivité",
    "seeders": "seeders",
    "leechers": "leechers",
    "disk_free": "espace libre",
    "torrent_count": "nombre de torrents",
}
DUPLICATE_LABELS = {
    "same_files": "mêmes fichiers",
    "same_title": "même titre",
    "episode_in_pack": "épisode couvert par un pack",
}
OP_SYMBOLS = {">=": "≥", "<=": "≤", "!=": "≠", "==": "=", ">": ">", "<": "<"}


# Facts ----------------------------------------------------------------------------------------------------


def _facts(session: Session, torrents: list[Torrent], catalog: Catalog, now: int) -> dict[str, dict]:
    windows = {w: analytics.window_uploads(session, torrents, analytics.WINDOWS[w], now) for w in analytics.WINDOWS}
    facts = {}
    for t in torrents:
        f = {
            "tracker": catalog.name(t.tracker),
            "category": t.category,
            "tag": {tag.strip() for tag in t.tags.split(",") if tag.strip()},
            "name": t.name,
            "state": t.state,
            "hnr": catalog.hnr(t, now).status.value,
            "unregistered": t.unregistered,
            "size": t.size,
            "uploaded": t.uploaded,
            "ratio": t.ratio,
            "seed_time": t.seeding_time,
            "age": max(now - t.added_on, 0),
            "inactive": max(now - t.last_activity, 0) if t.last_activity > 0 else max(now - t.added_on, 0),
            "seeders": t.num_complete,
            "leechers": t.num_incomplete,
        }
        for w, key in (("24h", "24h"), ("7d", "7d"), ("30d", "30d"), ("all", "all")):
            uploaded, days = windows[w][t.hash]
            if w != "all":
                f[f"upload_{key}"] = uploaded
            f[f"efficiency_{key}"] = uploaded / t.size / days if t.size and days else 0.0
        facts[t.hash] = f
    return facts


def _format(field_name: str, value) -> str:
    from seedkit.ruleset import FIELDS

    kind = FIELDS[field_name]
    if kind == "size":
        return human_size(value)
    if kind == "duration":
        return human_duration(value)
    if kind == "float":
        return number(value, 4 if field_name.startswith("efficiency") else 2)
    return str(value)


# Evaluation ----------------------------------------------------------------------------------------------


@dataclass
class Match:
    torrent: Torrent
    tracker: str
    rule: Rule
    reasons: list[str]


@dataclass
class RuleResult:
    rule: Rule
    matches: list[Match] = field(default_factory=list)  # selected and allowed
    blocked: dict[str, int] = field(default_factory=dict)  # matched but kept back by a safety gate
    inactive: str = ""  # why a global condition switched the rule off

    @property
    def size(self) -> int:
        return sum(m.torrent.size for m in self.matches)


@dataclass
class Evaluation:
    results: list[RuleResult]
    candidates: list[Match]  # unique torrents, first matching rule wins
    protected: dict[str, str]  # hash → protect rule name
    disk_free: int | None
    groups: dict[str, list[duplicates.Group]]
    efficiency: dict[str, float]

    @property
    def size(self) -> int:
        return sum(m.torrent.size for m in self.candidates)

    def by_rule(self) -> dict[str, list[Match]]:
        """Candidates grouped by the rule that claimed them (a torrent appears once)."""
        groups: dict[str, list[Match]] = {}
        for m in self.candidates:
            groups.setdefault(m.rule.name, []).append(m)
        return groups

    def result(self, name: str) -> RuleResult | None:
        return next((r for r in self.results if r.rule.name == name), None)


class _Context:
    def __init__(self, facts, globals_, groups, efficiency):
        self.facts = facts
        self.globals = globals_
        self.groups = groups
        self.efficiency = efficiency
        self._redundant: dict[tuple, dict[str, tuple]] = {}

    def redundant(self, kind: str, strategy: str) -> dict[str, tuple]:
        """hash → (group, kept units) for every torrent that is a redundant copy."""
        key = (kind, strategy)
        if key not in self._redundant:
            result = {}
            for group in self.groups[kind]:
                kept = group.kept(strategy, self.efficiency)
                for unit in group.redundant(strategy, self.efficiency):
                    for t in unit.torrents:
                        result.setdefault(t.hash, (group, kept))
            self._redundant[key] = result
        return self._redundant[key]


def _short(name: str, width: int = 48) -> str:
    return name if len(name) <= width else name[: width - 1] + "…"


def _compare(op: str, actual, expected) -> bool:
    if op == "range":
        return expected[0] <= actual <= expected[1]
    return {
        ">=": actual >= expected,
        "<=": actual <= expected,
        ">": actual > expected,
        "<": actual < expected,
        "==": actual == expected,
        "!=": actual != expected,
    }[op]


def _describe(leaf: Leaf) -> str:
    label = tr(FIELD_LABELS[leaf.field])
    if leaf.op == "range":
        return f"{label} {_format(leaf.field, leaf.value[0])}–{_format(leaf.field, leaf.value[1])}"
    if leaf.op in OP_SYMBOLS:
        return f"{label} {OP_SYMBOLS[leaf.op]} {_format(leaf.field, leaf.value)}"
    if leaf.field == "duplicate":
        return f"{label} : " + ", ".join(tr(DUPLICATE_LABELS[k]) for k in leaf.value)
    if leaf.op == "eq":
        return label if leaf.value else tr("pas {label}", label=label)
    if leaf.op == "regex":
        return f"{label} ~ {leaf.value.pattern}"
    if leaf.op == "contains":
        return f"{label} ∋ « {leaf.raw} »"
    return f"{label} = {', '.join(leaf.value)}"


def _leaf(leaf: Leaf, facts: dict, rule: Rule, ctx: _Context) -> tuple[bool, list[str]]:
    if leaf.field == "duplicate":
        for kind in leaf.value:
            strategy = rule.keep or DEFAULT_KEEP[kind]
            hit = ctx.redundant(kind, strategy).get(facts["hash"])
            if hit:
                _, kept = hit
                names = ", ".join(f"« {_short(u.label)} »" for u in kept[:2]) + (" …" if len(kept) > 2 else "")
                return True, [tr("doublon ({kind}) : on garde {names}", kind=tr(DUPLICATE_LABELS[kind]), names=names)]
        return False, []
    actual = ctx.globals[leaf.field] if leaf.is_global else facts[leaf.field]
    if actual is None:
        return False, []
    if leaf.op in OP_SYMBOLS or leaf.op == "range":
        ok = _compare(leaf.op, actual, leaf.value)
        if leaf.op == "range":
            text = f"{tr(FIELD_LABELS[leaf.field])} {_format(leaf.field, actual)} ∈ {_describe(leaf).split(' ', 1)[1]}"
        else:
            text = (
                f"{tr(FIELD_LABELS[leaf.field])} {_format(leaf.field, actual)} "
                f"{OP_SYMBOLS[leaf.op]} {_format(leaf.field, leaf.value)}"
            )
        return ok, [text]
    if leaf.op == "eq":
        return actual == leaf.value, [_describe(leaf)]
    if leaf.op == "regex":
        return bool(leaf.value.search(actual)), [_describe(leaf)]
    if leaf.op == "contains":
        return leaf.value in actual.casefold(), [_describe(leaf)]
    if leaf.field == "tag":
        return bool(actual & set(leaf.value)), [_describe(leaf)]
    return actual in leaf.value, [_describe(leaf)]


def _eval(cond, facts: dict, rule: Rule, ctx: _Context) -> tuple[bool, list[str]]:
    if cond is None:
        return True, []
    if isinstance(cond, Leaf):
        return _leaf(cond, facts, rule, ctx)
    if isinstance(cond, All):
        reasons = []
        for item in cond.items:
            ok, why = _eval(item, facts, rule, ctx)
            if not ok:
                return False, []
            reasons += why
        return True, reasons
    if isinstance(cond, Any):
        for item in cond.items:
            ok, why = _eval(item, facts, rule, ctx)
            if ok:
                return True, why
        return False, []
    if isinstance(cond, Not):
        ok, _ = _eval(cond.item, facts, rule, ctx)
        return (not ok), ([] if ok else [tr("pas : {what}", what=_summary(cond.item))])
    raise TypeError(cond)


def _summary(cond) -> str:
    if isinstance(cond, Leaf):
        return _describe(cond)
    if isinstance(cond, All):
        return " + ".join(_summary(c) for c in cond.items)
    if isinstance(cond, Any):
        return " | ".join(_summary(c) for c in cond.items)
    return tr("pas : {what}", what=_summary(cond.item))


def _global_gate(rule: Rule, ctx: _Context) -> str:
    """Empty string when the rule's global conditions hold, otherwise why the rule is idle."""
    top = rule.when.items if isinstance(rule.when, All) else ()
    for leaf in top:
        if not isinstance(leaf, Leaf) or not leaf.is_global:
            continue
        actual = ctx.globals[leaf.field]
        if actual is None:
            return tr("{label} inconnu", label=tr(FIELD_LABELS[leaf.field]))
        if not _compare(leaf.op, actual, leaf.value):
            return tr(
                "inactive : {label} vaut {value}",
                label=tr(FIELD_LABELS[leaf.field]),
                value=_format(leaf.field, actual),
            )
    return ""


def evaluate(session: Session, ruleset: RuleSet, now: int | None = None) -> Evaluation:
    now = now or int(time.time())
    catalog = Catalog.load(session)
    torrents = analytics.active_torrents(session)
    facts = _facts(session, torrents, catalog, now)
    for h, f in facts.items():
        f["hash"] = h
    efficiency = {h: f["efficiency_all"] for h, f in facts.items()}
    groups = duplicates.find(session, torrents)
    disk_free = free_space(session)
    ctx = _Context(facts, {"disk_free": disk_free, "torrent_count": len(torrents)}, groups, efficiency)

    protected: dict[str, str] = {}
    for rule in ruleset.protect:
        if not rule.enabled:
            continue
        for t in torrents:
            if t.hash not in protected and _eval(rule.when, facts[t.hash], rule, ctx)[0]:
                protected[t.hash] = rule.name

    results, candidates, claimed = [], [], set()
    for rule in ruleset.rules:
        result = RuleResult(rule)
        results.append(result)
        if not rule.enabled:
            result.inactive = tr("désactivée")
            continue
        result.inactive = _global_gate(rule, ctx)
        if result.inactive:
            continue
        allowed = []
        for t in torrents:
            ok, reasons = _eval(rule.when, facts[t.hash], rule, ctx)
            if not ok:
                continue
            reasons = [r for r in reasons if r]
            if t.hash in protected:
                why = tr("protégé par « {name} »", name=protected[t.hash])
            else:
                ok, why = eligible(t, catalog, now)
                why = "" if ok else tr(why)
            if why:
                result.blocked[why] = result.blocked.get(why, 0) + 1
                continue
            allowed.append(Match(t, catalog.name(t.tracker), rule, reasons))
        result.matches = _select(rule, allowed, facts, disk_free)
        for m in result.matches:
            if m.torrent.hash not in claimed:
                claimed.add(m.torrent.hash)
                candidates.append(m)
    return Evaluation(results, candidates, protected, disk_free, groups, efficiency)


def _select(rule: Rule, matches: list[Match], facts: dict, disk_free: int | None) -> list[Match]:
    sel = rule.select
    if sel is None:
        return sorted(matches, key=lambda m: -m.torrent.size)
    if sel.order_by:
        matches = sorted(matches, key=lambda m: facts[m.torrent.hash][sel.order_by], reverse=sel.descending)
    if sel.until_free is not None:
        needed = sel.until_free - (disk_free or 0)
        chosen, freed = [], 0
        for m in matches:
            if freed >= needed:
                break
            chosen.append(m)
            freed += m.torrent.size
        matches = chosen
    if sel.limit is not None:
        matches = matches[: sel.limit]
    return matches


# Helpers for the UI ---------------------------------------------------------------------------------------


def field_reference() -> list[tuple[str, str, str]]:
    """(field, kind, label) for the editor's reference panel."""
    from seedkit.ruleset import FIELDS

    return [(name, kind, tr(FIELD_LABELS[name])) for name, kind in FIELDS.items()]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "rule"
