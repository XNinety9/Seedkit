"""Cleanup rules file format: parsing and validation (no evaluation here, see engine.py).

A rules file is YAML:

    version: 1
    protect:            # never proposed, whatever the rules say
      - name: Favorites
        when: { tag: keep }
    rules:
      - name: Dormant
        when: { tracker: Acme, seed_time: ">= 45d", upload_30d: "< 500 MiB" }
        delete_files: true
        auto: false     # true = delete automatically after `grace` (needs SEEDKIT_ALLOW_DELETE)
        grace: 3d
      - name: Season packs win
        when: { duplicate: episode_in_pack }
      - name: Low disk
        when: { disk_free: "< 200 GiB" }
        select: { order_by: efficiency_30d, until_free: 350 GiB }

Conditions in a `when` mapping must all be true; `any: [...]`, `all: [...]` and `not: {...}` combine them.
"""

import re
from dataclasses import dataclass, field

import yaml

from seedkit.i18n import t as tr

# Field kinds -----------------------------------------------------------------------------------------------

SIZE_UNITS = {
    "b": 1, "o": 1,
    "kib": 1024, "kio": 1024, "ko": 1024, "kb": 1000,
    "mib": 1024**2, "mio": 1024**2, "mo": 1024**2, "mb": 1000**2,
    "gib": 1024**3, "gio": 1024**3, "go": 1024**3, "gb": 1000**3,
    "tib": 1024**4, "tio": 1024**4, "to": 1024**4, "tb": 1000**4,
}  # fmt: skip
DURATION_UNITS = {
    "s": 1, "min": 60, "h": 3600, "d": 86400, "j": 86400, "w": 7 * 86400, "sem": 7 * 86400,
    "mo": 30 * 86400, "mois": 30 * 86400, "y": 365 * 86400, "an": 365 * 86400, "ans": 365 * 86400,
}  # fmt: skip

NUMERIC_KINDS = {"size", "duration", "float", "int"}

# Torrent fields: name → kind. Windowed fields exist for 24h, 7d, 30d (and "all" for efficiency).
TORRENT_FIELDS = {
    "tracker": "str",
    "category": "str",
    "tag": "tags",
    "name": "text",
    "state": "str",
    "hnr": "str",
    "unregistered": "bool",
    "duplicate": "duplicate",
    "size": "size",
    "uploaded": "size",
    "upload_24h": "size",
    "upload_7d": "size",
    "upload_30d": "size",
    "ratio": "float",
    "efficiency_24h": "float",
    "efficiency_7d": "float",
    "efficiency_30d": "float",
    "efficiency_all": "float",
    "seed_time": "duration",
    "age": "duration",
    "inactive": "duration",
    "seeders": "int",
    "leechers": "int",
}
GLOBAL_FIELDS = {"disk_free": "size", "torrent_count": "int"}
FIELDS = TORRENT_FIELDS | GLOBAL_FIELDS

DUPLICATE_KINDS = ("same_files", "episode_in_pack", "same_title")
KEEP_STRATEGIES = (
    "highest_quality", "best_ratio", "best_efficiency", "most_seeders", "oldest", "newest", "largest", "smallest",
)  # fmt: skip
DEFAULT_KEEP = {"same_files": "best_ratio", "same_title": "highest_quality", "episode_in_pack": "pack"}
HNR_VALUES = ("safe", "pending", "at_risk", "no_rule", "downloading")
OPERATORS = (">=", "<=", "!=", "==", ">", "<")
DEFAULT_GRACE = 3 * 86400


class RuleSetError(Exception):
    def __init__(self, errors: list[tuple[str, str]]):
        self.errors = errors  # (path, message)
        super().__init__("; ".join(f"{p}: {m}" for p, m in errors))


# Model -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Leaf:
    field: str
    op: str  # for numbers: one of OPERATORS or "range"; "in" for lists; "eq"; "regex"
    value: object
    raw: object

    @property
    def is_global(self) -> bool:
        return self.field in GLOBAL_FIELDS


@dataclass(frozen=True)
class All:
    items: tuple


@dataclass(frozen=True)
class Any:
    items: tuple


@dataclass(frozen=True)
class Not:
    item: object


@dataclass(frozen=True)
class Select:
    order_by: str | None = None
    descending: bool = False
    until_free: int | None = None
    limit: int | None = None


@dataclass(frozen=True)
class Rule:
    name: str
    when: object | None
    delete_files: bool = True
    keep: str | None = None
    select: Select | None = None
    auto: bool = False
    grace: int = DEFAULT_GRACE
    enabled: bool = True

    def leaves(self) -> list[Leaf]:
        return list(_leaves(self.when))

    @property
    def duplicate_kinds(self) -> set[str]:
        kinds = set()
        for leaf in self.leaves():
            if leaf.field == "duplicate":
                kinds |= set(leaf.value)
        return kinds


@dataclass(frozen=True)
class RuleSet:
    version: int = 1
    protect: tuple[Rule, ...] = ()
    rules: tuple[Rule, ...] = field(default_factory=tuple)


def _leaves(cond):
    if cond is None:
        return
    if isinstance(cond, Leaf):
        yield cond
    elif isinstance(cond, (All, Any)):
        for item in cond.items:
            yield from _leaves(item)
    elif isinstance(cond, Not):
        yield from _leaves(cond.item)


# Value parsing ------------------------------------------------------------------------------------------------


def parse_size(text) -> int:
    if isinstance(text, (int, float)):
        return int(text)
    m = re.fullmatch(r"\s*([\d.,]+)\s*([a-zA-Z]*)\s*", str(text))
    if not m:
        raise ValueError(tr("taille invalide : {value} (ex. « 500 MiB », « 1.5 To »)", value=repr(text)))
    number, unit = float(m.group(1).replace(",", ".")), (m.group(2) or "b").lower()
    if unit not in SIZE_UNITS:
        raise ValueError(tr("unité de taille inconnue : {unit}", unit=repr(m.group(2))))
    return int(number * SIZE_UNITS[unit])


def parse_duration(text) -> int:
    if isinstance(text, (int, float)):
        return int(text)
    m = re.fullmatch(r"\s*([\d.,]+)\s*([a-zA-Z]+)\s*", str(text))
    if not m or m.group(2).lower() not in DURATION_UNITS:
        raise ValueError(tr("durée invalide : {value} (ex. « 45d », « 12h », « 2w »)", value=repr(text)))
    return int(float(m.group(1).replace(",", ".")) * DURATION_UNITS[m.group(2).lower()])


def _scalar(kind: str, text):
    if kind == "size":
        return parse_size(text)
    if kind == "duration":
        return parse_duration(text)
    if kind == "int":
        return int(str(text).strip())
    return float(str(text).strip().replace(",", "."))


def _numeric(kind: str, raw) -> tuple[str, object]:
    if isinstance(raw, bool):
        raise ValueError(tr("valeur numérique attendue"))
    if isinstance(raw, (int, float)) and kind in ("int", "float"):
        return "==", raw
    text = str(raw).strip()
    if ".." in text:
        low, high = text.split("..", 1)
        return "range", (_scalar(kind, low), _scalar(kind, high))
    for op in OPERATORS:
        if text.startswith(op):
            return op, _scalar(kind, text[len(op) :])
    if kind in ("int", "float"):
        return "==", _scalar(kind, text)
    raise ValueError(tr("opérateur manquant : écris par exemple « >= {value} » ou « < {value} »", value=text))


def _leaf(name: str, raw) -> Leaf:
    kind = FIELDS[name]
    if kind in NUMERIC_KINDS:
        op, value = _numeric(kind, raw)
        return Leaf(name, op, value, raw)
    if kind == "bool":
        if not isinstance(raw, bool):
            raise ValueError(tr("valeur attendue : true ou false"))
        return Leaf(name, "eq", raw, raw)
    if kind == "text":
        text = str(raw)
        if text.startswith("~"):
            try:
                return Leaf(name, "regex", re.compile(text[1:].strip(), re.I), raw)
            except re.error as exc:
                raise ValueError(tr("expression régulière invalide : {error}", error=exc)) from exc
        return Leaf(name, "contains", text.casefold(), raw)
    values = [str(v) for v in raw] if isinstance(raw, list) else [str(raw)]
    if not values:
        raise ValueError(tr("liste vide"))
    if kind == "duplicate":
        unknown = [v for v in values if v not in DUPLICATE_KINDS]
        if unknown:
            raise ValueError(
                tr(
                    "type de doublon inconnu : {values} (attendu : {expected})",
                    values=", ".join(unknown),
                    expected=", ".join(DUPLICATE_KINDS),
                )
            )
    if name == "hnr":
        unknown = [v for v in values if v not in HNR_VALUES]
        if unknown:
            raise ValueError(
                tr(
                    "statut H&R inconnu : {values} (attendu : {expected})",
                    values=", ".join(unknown),
                    expected=", ".join(HNR_VALUES),
                )
            )
    return Leaf(name, "in", tuple(values), raw)


# Structure parsing --------------------------------------------------------------------------------------------


def _condition(raw, path: str, errors: list) -> object | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.append((path, tr("un dictionnaire de conditions est attendu")))
        return None
    items = []
    for key, value in raw.items():
        sub = f"{path}.{key}"
        if key in ("any", "all"):
            if not isinstance(value, list) or not value:
                errors.append((sub, tr("une liste non vide de conditions est attendue")))
                continue
            parts = [c for i, v in enumerate(value) if (c := _condition(v, f"{sub}[{i}]", errors)) is not None]
            items.append(Any(tuple(parts)) if key == "any" else All(tuple(parts)))
        elif key == "not":
            if (c := _condition(value, sub, errors)) is not None:
                items.append(Not(c))
        elif key in FIELDS:
            try:
                items.append(_leaf(key, value))
            except (ValueError, TypeError) as exc:
                errors.append((sub, str(exc)))
        else:
            errors.append((sub, tr("champ inconnu (champs possibles : {fields})", fields=", ".join(FIELDS))))
    return All(tuple(items))


def _rule(raw, path: str, errors: list, protect: bool) -> Rule | None:
    if not isinstance(raw, dict):
        errors.append((path, tr("une règle doit être un dictionnaire")))
        return None
    allowed = {"name", "when", "enabled"} if protect else {
        "name", "when", "enabled", "delete_files", "keep", "select", "auto", "grace",
    }  # fmt: skip
    for key in raw:
        if key not in allowed:
            errors.append(
                (f"{path}.{key}", tr("clé inconnue (clés possibles : {keys})", keys=", ".join(sorted(allowed))))
            )
    name = str(raw.get("name") or "").strip()
    if not name:
        errors.append((f"{path}.name", tr("chaque règle doit avoir un nom")))
    if "when" not in raw:
        errors.append((f"{path}.when", tr("condition manquante")))
    when = _condition(raw.get("when"), f"{path}.when", errors)
    rule = Rule(name=name, when=when, enabled=bool(raw.get("enabled", True)))
    if protect:
        return rule

    kinds = rule.duplicate_kinds
    keep = raw.get("keep")
    if keep is not None:
        valid = ("pack",) if kinds == {"episode_in_pack"} else KEEP_STRATEGIES
        if not kinds:
            errors.append((f"{path}.keep", tr("« keep » n'a de sens qu'avec une condition « duplicate »")))
        elif keep not in valid:
            errors.append((f"{path}.keep", tr("stratégie inconnue (possibles : {values})", values=", ".join(valid))))
    select = None
    if (sel := raw.get("select")) is not None:
        select = _select(sel, f"{path}.select", errors)
    grace = DEFAULT_GRACE
    if "grace" in raw:
        try:
            grace = parse_duration(raw["grace"])
        except ValueError as exc:
            errors.append((f"{path}.grace", str(exc)))
    for key in ("auto", "delete_files", "enabled"):
        if key in raw and not isinstance(raw[key], bool):
            errors.append((f"{path}.{key}", tr("valeur attendue : true ou false")))
    return Rule(
        name=name,
        when=when,
        delete_files=raw.get("delete_files", True) is True,
        keep=keep,
        select=select,
        auto=raw.get("auto", False) is True,
        grace=grace,
        enabled=raw.get("enabled", True) is not False,
    )


def _select(raw, path: str, errors: list) -> Select | None:
    if not isinstance(raw, dict):
        errors.append((path, tr("un dictionnaire est attendu")))
        return None
    order_by, descending, until_free, limit = None, False, None, None
    for key, value in raw.items():
        if key == "order_by":
            text = str(value).strip()
            descending = text.startswith("-")
            order_by = text.lstrip("-")
            if TORRENT_FIELDS.get(order_by) not in NUMERIC_KINDS:
                numeric = [f for f, k in TORRENT_FIELDS.items() if k in NUMERIC_KINDS]
                errors.append((f"{path}.order_by", tr("champ numérique attendu ({fields})", fields=", ".join(numeric))))
        elif key == "until_free":
            try:
                until_free = parse_size(value)
            except ValueError as exc:
                errors.append((f"{path}.until_free", str(exc)))
        elif key == "limit":
            if not isinstance(value, int) or value <= 0:
                errors.append((f"{path}.limit", tr("entier positif attendu")))
            else:
                limit = value
        else:
            errors.append((f"{path}.{key}", tr("clé inconnue (order_by, until_free, limit)")))
    return Select(order_by, descending, until_free, limit)


def parse(text: str) -> RuleSet:
    """Parse and validate a rules file. Raises RuleSetError listing every problem found."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = tr("ligne {n}", n=mark.line + 1) if mark else "YAML"
        raise RuleSetError([(where, tr("YAML invalide : {error}", error=getattr(exc, "problem", exc)))]) from exc
    if not isinstance(data, dict):
        raise RuleSetError([(tr("racine"), tr("le fichier doit contenir « version », « protect » et « rules »"))])
    errors: list[tuple[str, str]] = []
    for key in data:
        if key not in ("version", "protect", "rules"):
            errors.append((str(key), tr("clé inconnue (version, protect, rules)")))
    if data.get("version", 1) != 1:
        errors.append(("version", tr("seule la version 1 est prise en charge")))
    sections = {}
    for section in ("protect", "rules"):
        raw = data.get(section) or []
        if not isinstance(raw, list):
            errors.append((section, tr("une liste de règles est attendue")))
            raw = []
        sections[section] = [
            r for i, item in enumerate(raw) if (r := _rule(item, f"{section}[{i}]", errors, section == "protect"))
        ]
    names = [r.name for r in sections["rules"] if r.name]
    for duplicate in {n for n in names if names.count(n) > 1}:
        errors.append(("rules", tr("nom de règle en double : « {name} »", name=duplicate)))
    if errors:
        raise RuleSetError(errors)
    return RuleSet(1, tuple(sections["protect"]), tuple(sections["rules"]))
