"""Terminal UI for seedkit: a read-only client of the seedkit server's JSON API."""

from __future__ import annotations

from typing import ClassVar

import requests
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.timer import Timer
from textual.widgets import DataTable, Digits, Footer, Input, Sparkline, Static, TabbedContent, TabPane

from seedkit import i18n
from seedkit.i18n import t as tr
from seedkit.web.format import ago, human_duration, human_size, number, ratio

SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]
STATUS = {
    "safe": ("#0ca30c", "H&R OK"),
    "pending": ("#fab219", "H&R en cours"),
    "at_risk": ("#d03b3b", "H&R en danger"),
    "no_rule": ("#5b6475", "sans règle"),
    "downloading": ("#3987e5", "téléchargement"),
}
ACCENT, ACCENT_2, MUTED = "#34d399", "#22d3ee", "#6c7587"
EIGHTHS = " ▏▎▍▌▋▊▉█"

THEME = Theme(
    name="seedkit",
    primary=ACCENT,
    secondary=ACCENT_2,
    accent="#a78bfa",
    warning="#fab219",
    error="#e5484d",
    success="#0ca30c",
    foreground="#eef1f6",
    background="#090b10",
    surface="#11141b",
    panel="#161a23",
    dark=True,
)


# Drawing helpers ---------------------------------------------------------------


def _mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb, strict=True))


def hbar(fraction: float, width: int, start: str = ACCENT, end: str = ACCENT_2, track: str = "#1d2230") -> Text:
    """Horizontal bar with eighth-block precision and a color gradient."""
    fraction = max(0.0, min(fraction, 1.0))
    cells = fraction * width
    full = int(cells)
    text = Text()
    for i in range(full):
        text.append("█", style=_mix(start, end, i / max(width - 1, 1)))
    if full < width:
        part = EIGHTHS[round((cells - full) * 8)]
        if part.strip():
            text.append(part, style=f"{_mix(start, end, full / max(width - 1, 1))} on {track}")
        else:
            text.append(" ", style=f"on {track}")
        text.append(" " * (width - full - 1), style=f"on {track}")
    return text


def stacked(parts: list[tuple[float, str]], width: int) -> Text:
    """Stacked bar of (value, color), each part at least one cell when non-zero."""
    total = sum(v for v, _ in parts) or 1
    text = Text()
    cells = [(max(round(v / total * width), 1) if v else 0, c) for v, c in parts]
    overflow = sum(n for n, _ in cells) - width
    if overflow > 0:  # trim the largest part
        i = max(range(len(cells)), key=lambda k: cells[k][0])
        cells[i] = (cells[i][0] - overflow, cells[i][1])
    for n, color in cells:
        text.append("█" * n, style=color)
    return text


def size_parts(n: float) -> tuple[str, str]:
    value, _, unit = human_size(n).partition(" ")
    return value.replace(" ", ""), unit


def dot(color: str, label: str = "", style: str = "") -> Text:
    """Colored square marker followed by a label in text color (the label never takes the series color)."""
    return Text().append("■ ", style=color).append(label, style=style)


# API ------------------------------------------------------------------------------


class Api:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.http = requests.Session()

    def get(self, path: str, **params):
        params = {"lang": i18n.get_lang(), **params}
        r = self.http.get(f"{self.url}/api/{path}", params=params, timeout=20)
        r.raise_for_status()
        return r.json()


# Widgets ----------------------------------------------------------------------------


class Card(Vertical):
    def __init__(self, title: str, *children, **kwargs):
        super().__init__(*children, **kwargs)
        self.border_title = title


class Brand(Static):
    def render(self) -> Text:
        text = Text()
        for i, ch in enumerate(" seedkit"):
            text.append(ch, style=f"bold {_mix(ACCENT, ACCENT_2, i / 7)}")
        return text + Text("  " + tr("boîte à outils seedbox"), style=MUTED)


class SeedkitTUI(App):
    TITLE = "seedkit"
    CSS = """
    Screen { background: $background; }
    #top { height: 3; padding: 1 2 0 2; background: $surface; }
    #brand { width: auto; }
    #status { width: 1fr; text-align: right; color: $text-muted; }
    TabbedContent { height: 1fr; }
    Card {
        border: round #262c3a; border-title-color: #a8b0bf; border-title-style: bold;
        background: $surface; padding: 0 1; height: auto; margin: 0 1 1 0;
    }
    .row { height: auto; }
    .row > Card { width: 1fr; }
    #hero { width: 2fr; }
    #hero Digits { color: $primary; width: auto; }
    .unit { color: $secondary; padding: 2 0 0 1; width: auto; text-style: bold; }
    .big { height: auto; }
    .tile Digits { color: $foreground; width: auto; }
    .tile .unit { color: #a8b0bf; }
    Sparkline { height: 3; margin-top: 1; }
    #spark-hour > .sparkline--max-color { color: $primary; }
    #spark-hour > .sparkline--min-color { color: #1f6e57; }
    #spark-day > .sparkline--max-color { color: $secondary; }
    #spark-day > .sparkline--min-color { color: #19606b; }
    #daily-spark { height: 8; }
    #daily-spark > .sparkline--max-color { color: $secondary; }
    #daily-spark > .sparkline--min-color { color: #2a5bd7; }
    .meta { color: #a8b0bf; height: auto; }
    .muted { color: $text-muted; }
    #dash, #watch, #trackers-pane { scrollbar-gutter: stable; }
    #dash { padding: 1 1 0 2; }
    .placeholder { color: $text-muted; height: 3; content-align: center middle; }
    #search { margin: 1 2 0 2; border: round #262c3a; }
    #search:focus { border: round $primary; }
    DataTable { height: 1fr; margin: 0 2 1 2; background: $surface; }
    DataTable > .datatable--header { background: $panel; color: $text-muted; text-style: bold; }
    DataTable > .datatable--cursor { background: #1f3b33; }
    #torrent-info { margin: 1 2 0 2; color: $text-muted; height: 1; }
    #watch, #trackers-pane { padding: 1 1 0 2; }
    """
    BINDINGS: ClassVar = [
        Binding("q", "quit", tr("Quitter")),
        Binding("r", "refresh", tr("Rafraîchir")),
        Binding("slash", "search", tr("Rechercher")),
        Binding("1", "tab('dash-tab')", tr("Tableau de bord"), show=False),
        Binding("2", "tab('torrents-tab')", tr("Torrents"), show=False),
        Binding("3", "tab('watch-tab')", tr("À surveiller"), show=False),
        Binding("4", "tab('trackers-tab')", tr("Trackers"), show=False),
        Binding("w", "cycle_window", tr("Fenêtre")),
    ]
    WINDOWS: ClassVar = ["24h", "7d", "30d", "all"]
    WINDOW_LABELS: ClassVar = {"24h": "24 h", "7d": "7 jours", "30d": "30 jours", "all": "depuis l'ajout"}
    SORT_KEYS: ClassVar = {
        "Torrent": "name",
        "Taille": "size",
        "Ratio": "ratio",
        "Upload": "uploaded",
        "Seed": "seed",
        "H&R": "hnr",
    }

    def __init__(self, url: str):
        super().__init__()
        self.api = Api(url)
        self.window: str | None = None
        self.sort, self.desc = "added", True
        self._search_timer: Timer | None = None
        self._last: tuple | None = None

    # Layout ---------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Brand(id="brand")
            yield Static(tr("connexion…"), id="status")
        with TabbedContent(initial="dash-tab"):
            with TabPane(tr("Tableau de bord"), id="dash-tab"), VerticalScroll(id="dash"):
                with Horizontal(classes="row"):
                    with Card(tr("Uploadé au total"), id="hero"):
                        with Horizontal(classes="big"):
                            yield Digits("—", id="total")
                            yield Static("", classes="unit", id="total-unit")
                        yield Static("", classes="meta", id="hero-meta")
                    with Card(tr("Upload 24 h"), classes="tile"):
                        with Horizontal(classes="big"):
                            yield Digits("—", id="up24")
                            yield Static("", classes="unit", id="up24-unit")
                        yield Sparkline([], id="spark-hour")
                    with Card(tr("Upload 7 jours"), classes="tile"):
                        with Horizontal(classes="big"):
                            yield Digits("—", id="up7")
                            yield Static("", classes="unit", id="up7-unit")
                        yield Sparkline([], id="spark-day")
                with Horizontal(classes="row"):
                    with Card(tr("Upload par jour · 30 jours"), id="daily-card"):
                        yield Sparkline([], id="daily-spark")
                        yield Static("", classes="muted", id="daily-axis")
                    with Card(tr("Espace par tracker")):
                        yield Static("", id="disk")
                with Horizontal(classes="row"):
                    with Card(tr("Santé H&R")):
                        yield Static("", id="health")
                    with Card(tr("Âge du stock")):
                        yield Static("", id="age")
                with Horizontal(classes="row"):
                    with Card(tr("Meilleurs seeds"), id="top-card"):
                        yield Static("", id="rank-top")
                    with Card(tr("Moins rentables"), id="flop-card"):
                        yield Static("", id="rank-flop")
            with TabPane(tr("Torrents"), id="torrents-tab"), Vertical():
                yield Input(placeholder=tr("Rechercher un torrent…"), id="search")
                yield Static("", id="torrent-info")
                yield DataTable(id="torrents", cursor_type="row", zebra_stripes=True)
            with TabPane(tr("À surveiller"), id="watch-tab"):
                yield VerticalScroll(id="watch")
            with TabPane(tr("Trackers"), id="trackers-tab"):
                yield VerticalScroll(id="trackers-pane")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = "seedkit"
        table = self.query_one("#torrents", DataTable)
        for label in ("Torrent", "Tracker", "Taille", "Ratio", "Upload", "Seed", "H&R"):
            table.add_column(tr(label), key=label)
        self.action_refresh()
        self.set_interval(60, self.action_refresh)

    # Actions ----------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.load_dashboard()
        self.load_torrents()

    def action_search(self) -> None:
        self.query_one(TabbedContent).active = "torrents-tab"
        self.query_one("#search", Input).focus()

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def action_cycle_window(self) -> None:
        current = self.window or "all"
        self.window = self.WINDOWS[(self.WINDOWS.index(current) + 1) % len(self.WINDOWS)]
        self.notify(tr("Classements sur {window}", window=tr(self.WINDOW_LABELS[self.window])), timeout=2)
        self.load_dashboard()

    @on(Input.Changed, "#search")
    def search_changed(self) -> None:
        if self._search_timer:
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.3, self.load_torrents)

    @on(DataTable.HeaderSelected)
    def sort_by(self, event: DataTable.HeaderSelected) -> None:
        key = self.SORT_KEYS.get(str(event.column_key.value))
        if not key:
            return
        self.desc = not self.desc if key == self.sort else key != "name"
        self.sort = key
        self.load_torrents()

    # Data loading (threads) ------------------------------------------------------------

    @work(thread=True, exclusive=True, group="dashboard")
    def load_dashboard(self) -> None:
        try:
            params = {"window": self.window} if self.window else {}
            data = self.api.get("dashboard", **params)
            status = self.api.get("status")
            watch = self.api.get("watch")
        except requests.RequestException as exc:
            self.call_from_thread(self.show_offline, exc)
            return
        self.call_from_thread(self.show_dashboard, data, status, watch)

    @work(thread=True, exclusive=True, group="torrents")
    def load_torrents(self) -> None:
        q = self.query_one("#search", Input).value
        try:
            data = self.api.get("torrents", q=q, sort=self.sort, desc=str(self.desc).lower(), per_page=500)
        except requests.RequestException:
            return
        self.call_from_thread(self.show_torrents, data)

    # Rendering ------------------------------------------------------------------------

    def show_offline(self, exc: Exception) -> None:
        self.query_one("#status", Static).update(
            Text(
                "● " + tr("serveur seedkit injoignable à {url} — lance « seedkit serve »", url=self.api.url),
                style="#ff6b6b",
            )
        )

    def on_resize(self) -> None:
        if self._last:
            self.call_after_refresh(self.show_dashboard, *self._last)

    def _spark(self, selector: str, values: list[int]) -> None:
        spark = self.query_one(selector, Sparkline)
        spark.data = values if len(values) >= 2 else [0, 0]
        spark.display = len(values) >= 2

    def show_dashboard(self, d: dict, status: dict, watch: list) -> None:
        self._last = (d, status, watch)
        self.window = self.window or d["window"]
        o = d["overview"]
        self._status(status, len(watch))

        value, unit = size_parts(o["uploaded"])
        self.query_one("#total", Digits).update(value)
        self.query_one("#total-unit", Static).update(unit)
        meta = Text()
        for label, val in (
            (tr("Ratio global") + " ", ratio(o["ratio"])),
            ("   ", number(o["count"])),
            (" " + tr("torrents") + "   ", human_size(o["size"])),
            (" " + tr("stockés") + "   ", str(o["active"])),
            (" " + tr("en upload actif"), ""),
        ):
            meta.append(label, style="#a8b0bf").append(val, style="bold #eef1f6")
        self.query_one("#hero-meta", Static).update(meta)

        for key, digits_id in (("uploaded_24h", "up24"), ("uploaded_7d", "up7")):
            value, unit = size_parts(o[key])
            self.query_one(f"#{digits_id}", Digits).update(value)
            self.query_one(f"#{digits_id}-unit", Static).update(unit)
        charts = d["charts"]
        self._spark("#spark-hour", charts["hourly"]["values"])
        self._spark("#spark-day", charts["daily_total"]["values"])

        daily = [sum(day) for day in zip(*[s["data"] for s in charts["daily"]["series"]], strict=True)]
        self._spark("#daily-spark", daily)
        labels = charts["daily"]["labels"]
        axis = Text(tr("Le graphique se construit : il faut au moins deux jours de collecte."), style=MUTED)
        if len(labels) >= 2:
            peak = tr("max {size}/jour", size=human_size(max(daily)))
            axis = Text(f"{labels[0][5:]} → {labels[-1][5:]}   {peak}", style=MUTED)
        self.query_one("#daily-axis", Static).update(axis)

        self._disk(d)
        self._health(d)
        self._age(charts["age"])
        self._ranking("#rank-top", "#top-card", d["top"], d, top=True)
        self._ranking("#rank-flop", "#flop-card", d["flop"], d, top=False)
        self._watch(watch)
        self._trackers(d)

    def _status(self, status: dict, watch_count: int) -> None:
        text = Text()
        if status["last_error"]:
            text.append(f"● {status['last_error']}", style="#ff6b6b")
        else:
            text.append("● ", style="#0ca30c").append(
                tr("Collecte {ago}", ago=ago(status["last_run"])) if status["last_run"] else tr("Aucune collecte"),
                style="#a8b0bf",
            )
        if watch_count:
            text.append("   ▲ " + tr("{n} à surveiller", n=watch_count), style="#fbc04a")
        if status["locks"]["delete"]:
            text.append("   🔒 " + tr("suppression verrouillée"), style="#fbc04a")
        self.query_one("#status", Static).update(text)

    def _width(self, selector: str, minus: int = 4) -> int:
        return max(self.query_one(selector).size.width - minus - 1, 10)

    def _disk(self, d: dict) -> None:
        disk = d["charts"]["disk"]
        width = self._width("#disk", 0)
        parts = [(v, SERIES[c % 5]) for v, c in zip(disk["values"], disk["colors"], strict=True)]
        text = stacked(parts, width) + Text("\n")
        total = sum(disk["values"]) or 1
        for label, v, c in zip(disk["labels"], disk["values"], disk["colors"], strict=True):
            line = dot(SERIES[c % 5], label)
            right = f"{human_size(v)} · " + tr("{pct} %", pct=number(100 * v / total))
            line.append(" " * max(width - len(line) - len(right), 1)).append(right, style="#a8b0bf")
            text.append("\n").append(line)
        self.query_one("#disk", Static).update(text)

    def _health(self, d: dict) -> None:
        width = self._width("#health", 0)
        text = Text()
        for s in d["trackers"]:
            head = dot(SERIES[s["slot"] % 5], s["name"], "bold")
            key = "{n} torrent · ratio {ratio}" if s["count"] == 1 else "{n} torrents · ratio {ratio}"
            right = tr(key, n=s["count"], ratio=ratio(s["ratio"]))
            head.append(" " * max(width - len(head) - len(right), 1)).append(right, style=MUTED)
            parts = [(s["statuses"].get(k, 0), STATUS[k][0]) for k in STATUS]
            text.append(head).append("\n").append(stacked(parts, width)).append("\n\n")
        legend = Text()
        for key, (color, label) in STATUS.items():
            if d["status_totals"].get(key):
                legend.append("■ ", style=color).append(f"{tr(label)} {d['status_totals'][key]}   ", style="#a8b0bf")
        self.query_one("#health", Static).update(text + legend)

    def _age(self, age: dict) -> None:
        width = self._width("#age", 26)
        top = max(age["values"]) or 1
        text = Text()
        for label, v, n in zip(age["labels"], age["values"], age["counts"], strict=True):
            text.append(f"{label:<11}", style="#a8b0bf")
            text.append(hbar(v / top, width, "#2a78d6", "#3987e5"))
            text.append(f" {human_size(v):>8} ", style="bold").append(f"{n:>4}\n", style=MUTED)
        self.query_one("#age", Static).update(text)

    def _ranking(self, selector: str, card: str, rows: list, d: dict, top: bool) -> None:
        self.query_one(card).border_subtitle = tr(self.WINDOW_LABELS.get(d["window"], d["window"]))
        width = self._width(selector, 0)
        best = (d["top"][0]["efficiency"] if d["top"] else 0) or 1
        medals = ["#f59e0b", "#9ca3af", "#c2410c"]
        slots = {t["name"]: t["slot"] for t in d["trackers"]}
        text = Text()
        if not rows:
            text.append(tr("Pas encore assez de recul."), style=MUTED)
        for i, r in enumerate(rows):
            rank_style = f"bold #090b10 on {medals[i]}" if top and i < 3 else "bold on #1d2230"
            eff = f"{number(r['efficiency'], 3)} {tr('×/j')}"
            name_width = width - len(eff) - 5
            name = r["name"] if len(r["name"]) <= name_width else r["name"][: name_width - 1] + "…"
            line = Text().append(f" {i + 1} ", style=rank_style).append(" ").append(name)
            line.append(" " * max(width - len(line) - len(eff), 1)).append(eff, style="bold")
            sub = Text("    ") + dot(SERIES[slots.get(r["tracker"], 4) % 5])
            sub.append(
                tr(
                    "{tracker} · {size} · {uploaded} uploadés",
                    tracker=r["tracker"],
                    size=human_size(r["size"]),
                    uploaded=human_size(r["window_uploaded"]),
                ),
                style=MUTED,
            )
            bar = (
                hbar(r["efficiency"] / best, width - 4)
                if top
                else hbar(r["efficiency"] / best, width - 4, "#64748b", "#94a3b8")
            )
            text.append(line).append("\n").append(sub).append("\n    ").append(bar).append("\n")
        self.query_one(selector, Static).update(text)

    def _watch(self, groups: list) -> None:
        pane = self.query_one("#watch", VerticalScroll)
        pane.remove_children()
        if not groups:
            pane.mount(
                Card(tr("Tout va bien"), Static(Text(tr("Aucun torrent en erreur, retiré ou en danger côté H&R. 👌"))))
            )
            return
        for g in groups:
            color = "#ff6b6b" if g["critical"] else "#fbc04a"
            text = Text()
            for item in g["items"]:
                status_color, label = STATUS[item["hnr"]["status"]]
                text.append("▲ ", style=color).append(item["name"], style="bold")
                text.append(f"\n   {item['tracker']} · {human_size(item['size'])} · ", style=MUTED)
                text.append(tr(label), style=status_color).append("\n")
            card = Card(f"{g['reason'][:1].upper()}{g['reason'][1:]} · {len(g['items'])}", Static(text))
            card.styles.border = ("round", color)
            pane.mount(card)

    def _trackers(self, d: dict) -> None:
        pane = self.query_one("#trackers-pane", VerticalScroll)
        pane.remove_children()
        for s in d["trackers"]:
            text = Text()
            text.append(tr("domaines") + "  ", style=MUTED).append(", ".join(s["domains"]) + "\n")
            for label, value in (
                (tr("torrents"), number(s["count"])),
                (tr("espace"), human_size(s["size"])),
                (tr("uploadé"), human_size(s["uploaded"])),
                (tr("upload 7 j"), human_size(s["uploaded_7d"])),
                (tr("ratio"), ratio(s["ratio"])),
            ):
                text.append(f"{label}  ", style=MUTED).append(f"{value}     ", style="bold")
            text.append("\n\n").append(stacked([(s["statuses"].get(k, 0), STATUS[k][0]) for k in STATUS], 60))
            text.append("\n")
            for key, (color, label) in STATUS.items():
                if s["statuses"].get(key):
                    text.append("■ ", style=color).append(f"{tr(label)} {s['statuses'][key]}   ", style="#a8b0bf")
            card = Card(s["name"], Static(text))
            card.styles.border = ("round", SERIES[s["slot"] % 5])
            pane.mount(card)

    def show_torrents(self, data: dict) -> None:
        table = self.query_one("#torrents", DataTable)
        table.clear()
        for t in data["rows"]:
            hnr = t["hnr"]
            color, label = STATUS[hnr["status"]]
            cell = Text().append("● ", style=color).append(tr(label))
            if hnr["status"] in ("pending", "at_risk") and hnr["progress"] is not None:
                cell = hbar(hnr["progress"], 10, color, color) + Text(" ")
                remaining = hnr["remaining_hours"]
                cell.append(human_duration(remaining * 3600) if remaining else f"{hnr['progress']:.0%}", style=color)
            table.add_row(
                Text(t["name"][:70] + ("…" if len(t["name"]) > 70 else "")),
                Text(t["tracker"], style="#a8b0bf"),
                Text(human_size(t["size"]), justify="right"),
                Text(ratio(t["ratio"]), justify="right", style="bold #3ccf6a" if t["ratio"] >= 1 else "bold"),
                Text(human_size(t["uploaded"]), justify="right", style="#a8b0bf"),
                Text(human_duration(t["seeding_time"]), justify="right", style="#a8b0bf"),
                cell,
                key=t["hash"],
            )
        arrow = "↓" if self.desc else "↑"
        info = Text(tr("{n} torrents · {size}", n=number(data["total"]), size=human_size(data["size"])), style="bold")
        sort_label = tr({v: k for k, v in self.SORT_KEYS.items()}.get(self.sort, "date d'ajout"))
        hint = tr("tri : {column} {arrow}   (clic sur un en-tête pour trier)", column=sort_label.lower(), arrow=arrow)
        info.append("   " + hint, style=MUTED)
        if data["pages"] > 1:
            info.append("   · " + tr("{n} premiers affichés", n=len(data["rows"])), style=MUTED)
        self.query_one("#torrent-info", Static).update(info)


def run(url: str) -> None:
    SeedkitTUI(url).run()


if __name__ == "__main__":  # pragma: no cover
    import sys

    run(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8337")
