"""The cleanup rules file: loading, default template, validated saving."""

import shutil
from pathlib import Path

from seedkit.config import Settings
from seedkit.i18n import get_lang
from seedkit.ruleset import RuleSet, RuleSetError, parse

TEMPLATE_FR = """\
# Règles de nettoyage seedkit — documentation : https://github.com/XNinety9/Seedkit#règles-de-nettoyage
#
# Par défaut, une règle se contente de PROPOSER des torrents dans l'écran Nettoyage.
# « auto: true » les supprime automatiquement après « grace » (3 jours par défaut), uniquement si
# SEEDKIT_ALLOW_DELETE=true. Dans tous les cas, un torrent dont le tracker n'a pas de règle H&R,
# ou dont le H&R n'est pas rempli, n'est jamais touché.
version: 1

# Les protections passent avant tout.
protect:
  - name: Tag « keep »
    when: { tag: keep }

rules:
  - name: Retirés du tracker
    when: { unregistered: true }

  - name: Épisodes couverts par un pack
    when: { duplicate: episode_in_pack }

  - name: Mêmes fichiers en double
    when: { duplicate: same_files }
    keep: best_ratio

  - name: Même titre, on garde la meilleure qualité
    when: { duplicate: same_title }
    keep: highest_quality

  - name: Dormants
    when:
      seed_time: ">= 60d"
      upload_30d: "< 100 MiB"
      seeders: ">= 3"

  - name: Disque presque plein
    when: { disk_free: "< 100 GiB" }
    select: { order_by: efficiency_30d, until_free: 200 GiB }
"""

TEMPLATE_EN = """\
# seedkit cleanup rules — documentation: https://github.com/XNinety9/Seedkit#règles-de-nettoyage
#
# By default a rule only PROPOSES torrents on the Cleanup screen.
# "auto: true" deletes them automatically after "grace" (3 days by default), only when
# SEEDKIT_ALLOW_DELETE=true. In every case, a torrent whose tracker has no H&R rule,
# or whose H&R is not met yet, is never touched.
version: 1

# Protections always win.
protect:
  - name: Keep tag
    when: { tag: keep }

rules:
  - name: Removed from the tracker
    when: { unregistered: true }

  - name: Episodes covered by a season pack
    when: { duplicate: episode_in_pack }

  - name: Same files twice
    when: { duplicate: same_files }
    keep: best_ratio

  - name: Same title, keep the best quality
    when: { duplicate: same_title }
    keep: highest_quality

  - name: Dormant
    when:
      seed_time: ">= 60d"
      upload_30d: "< 100 MiB"
      seeders: ">= 3"

  - name: Disk almost full
    when: { disk_free: "< 100 GiB" }
    select: { order_by: efficiency_30d, until_free: 200 GiB }
"""


def path(settings: Settings) -> Path:
    return settings.seedkit_cleanup_rules


def read(settings: Settings) -> str:
    p = path(settings)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(TEMPLATE_FR if get_lang() == "fr" else TEMPLATE_EN, encoding="utf-8")
    return p.read_text(encoding="utf-8")


def load(settings: Settings, create: bool = True) -> tuple[str, RuleSet | None, list[tuple[str, str]]]:
    """(text, ruleset or None, errors). With create=False a missing file means "no rules" and nothing is written
    (background jobs must not create the file: only the UI knows the user's language)."""
    if not create and not path(settings).exists():
        return "", RuleSet(), []
    text = read(settings)
    try:
        return text, parse(text), []
    except RuleSetError as exc:
        return text, None, exc.errors


def save(settings: Settings, text: str) -> RuleSet:
    """Validate then write; the previous version is kept next to it as .bak. Raises RuleSetError."""
    ruleset = parse(text)
    p = path(settings)
    if p.exists():
        shutil.copyfile(p, p.with_suffix(p.suffix + ".bak"))
    p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return ruleset
