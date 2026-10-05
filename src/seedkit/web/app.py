import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from seedkit import actions, analytics, autoclean, cleanup, engine, i18n, rulesfile, trackers, views
from seedkit.config import Settings, get_settings
from seedkit.db import Torrent, make_engine, make_sessionmaker
from seedkit.i18n import t as tr
from seedkit.rules import HNR_LABELS, HnrStatus
from seedkit.ruleset import RuleSetError
from seedkit.ruleset import parse as parse_rules
from seedkit.service import Service, last_smb_scan
from seedkit.web import api
from seedkit.web.format import FILTERS

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters.update(FILTERS)
templates.env.globals.update(
    _=i18n.markup,
    tr=i18n.t,
    LANGS=i18n.LANGS,
    HnrStatus=HnrStatus,
    HNR_LABELS=HNR_LABELS,
    WINDOWS=list(analytics.WINDOWS),
    STATUS_COLORS={
        "safe": "var(--status-good)",
        "pending": "var(--status-warning)",
        "at_risk": "var(--status-critical)",
        "no_rule": "var(--status-neutral)",
        "downloading": "var(--series-1)",
    },
)
templates.env.filters["ucfirst"] = lambda s: s[:1].upper() + s[1:] if s else s
templates.env.filters["combine_spark"] = lambda data, color: {**data, "color": color}
templates.env.filters["merge"] = lambda params, **kw: {
    k: v for k, v in ({**params, **kw}).items() if v not in ("", None)
}


def _back(request: Request, fallback: str = "/", **toast) -> RedirectResponse:
    url = request.headers.get("referer") or fallback
    url = url.split("?toast=")[0].split("&toast=")[0]
    if toast:
        url += ("&" if "?" in url else "?") + urlencode(toast)
    return RedirectResponse(url, status_code=303)


def _float(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError as exc:
        raise HTTPException(400, tr("Nombre invalide : {value}", value=repr(value))) from exc


def js_strings() -> dict:
    """Strings and number formats used by seedkit.js."""
    return {
        "locale": "fr-FR" if i18n.get_lang() == "fr" else "en-US",
        "units": ["o", "Ko", "Mo", "Go", "To", "Po"]
        if i18n.get_lang() == "fr"
        else ["B", "KiB", "MiB", "GiB", "TiB", "PiB"],
        "total": tr("Total : {value}"),
        "size": tr("Taille"),
        "upload_per_day": tr("Upload par jour ({window})"),
        "per_day": tr("/jour"),
        "le1": tr("≤ 1 Mo"),
        "torrents": tr("{n} torrents"),
        "upload": tr("Upload : {value}"),
        "size_value": tr("Taille : {value}"),
    }


def create_app(settings: Settings | None = None, start_jobs: bool = True, client=None) -> FastAPI:
    settings = settings or get_settings()
    db_engine = make_engine(settings.seedkit_db)
    sessions = make_sessionmaker(db_engine)
    service = Service(settings, sessions, client=client)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if start_jobs:
            scheduler = BackgroundScheduler()
            scheduler.add_job(
                service.collect,
                "interval",
                minutes=settings.seedkit_interval_minutes,
                next_run_time=datetime.now(),
                max_instances=1,
                coalesce=True,
            )
            scheduler.add_job(service.compact, "cron", hour=4, minute=17, max_instances=1, coalesce=True)
            scheduler.start()
        yield
        if scheduler:
            scheduler.shutdown(wait=False)
        db_engine.dispose()

    app = FastAPI(title="seedkit", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.service = service
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def language(request: Request, call_next):
        lang = i18n.resolve(
            request.query_params.get("lang") or request.cookies.get("seedkit_lang"),
            settings.seedkit_lang,
            request.headers.get("accept-language"),
        )
        i18n.set_lang(lang)
        request.state.lang = lang
        return await call_next(request)

    @app.get("/lang/{code}")
    def set_language(request: Request, code: str):
        if code not in i18n.LANGS:
            raise HTTPException(404)
        response = _back(request)
        response.set_cookie("seedkit_lang", code, max_age=365 * 86400, samesite="lax")
        return response

    app.include_router(api.router)

    def db(request: Request) -> Session:
        return request.app.state.sessions()

    def render(request: Request, name: str, page: str = "", **ctx) -> HTMLResponse:
        with db(request) as session:
            watch_count = len(analytics.watchlist(session))
        return templates.TemplateResponse(
            request,
            name,
            {
                "service": service,
                "settings": settings,
                "page": page,
                "watch_count": watch_count,
                "toast": request.query_params.get("toast"),
                "now": int(time.time()),
                "lang": i18n.get_lang(),
                "js_i18n": js_strings(),
                **ctx,
            },
        )

    def is_htmx(request: Request) -> bool:
        return request.headers.get("HX-Request") == "true"

    # Dashboard -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, window: str | None = None):
        if window is not None and window not in analytics.WINDOWS:
            raise HTTPException(400)
        with db(request) as session:
            data = views.dashboard(session, window)
        template = "partials/performance.html" if is_htmx(request) else "dashboard.html"
        return render(request, template, page="dashboard", d=data)

    # Torrents ----------------------------------------------------------------

    @app.get("/torrents", response_class=HTMLResponse)
    def torrents_page(
        request: Request,
        q: str = "",
        tracker: str = "",
        status: str = "",
        sort: str = "added",
        desc: bool = True,
        page: int = 1,
    ):
        with db(request) as session:
            data = views.torrents(session, q, tracker, status, sort, desc, page)
        params = {"q": q, "tracker": tracker, "status": status, "sort": sort, "desc": desc}
        template = "partials/torrents_table.html" if is_htmx(request) else "torrents.html"
        return render(request, template, page="torrents", d=data, params=params)

    # Watch list ----------------------------------------------------------------

    @app.get("/watch", response_class=HTMLResponse)
    def watch(request: Request):
        with db(request) as session:
            groups = views.watch_groups(session)
        return render(request, "watch.html", page="watch", groups=groups)

    @app.post("/watch/action")
    def watch_action(request: Request, action: str = Form(...), hashes: list[str] = Form(...)):
        handler = {"reannounce": actions.reannounce, "recheck": actions.recheck}.get(action)
        if handler is None:
            raise HTTPException(400)
        try:
            with db(request) as session:
                n = handler(service.client, session, settings, hashes)
        except actions.ActionsDisabled as exc:
            return _back(request, "/watch", toast=str(exc))
        return _back(request, "/watch", toast=tr("{n} torrent(s) : {action} envoyé", n=n, action=action))

    # Trackers & rules ------------------------------------------------------------

    @app.get("/trackers", response_class=HTMLResponse)
    def trackers_page(request: Request):
        with db(request) as session:
            catalog = analytics.Catalog.load(session)
            stats = analytics.tracker_stats(session, catalog=catalog)
            palette = views.Palette.build(stats)
            return render(
                request,
                "trackers.html",
                page="trackers",
                stats=stats,
                rules=catalog.rules,
                palette=palette,
                names=[s.name for s in stats],
                orphan_rules=trackers.rules_without_torrents(session),
                suggestions=trackers.suggest_merges(stats, catalog.aliases),
            )

    @app.post("/trackers/rule")
    def save_rule(
        request: Request,
        tracker: str = Form(...),
        min_seed_hours: str = Form(""),
        min_ratio: str = Form(""),
        satisfy: str = Form("any"),
        notes: str = Form(""),
    ):
        try:
            with db(request) as session:
                trackers.set_rule(session, tracker, _float(min_seed_hours), _float(min_ratio), satisfy, notes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _back(request, "/trackers", toast=tr("Règle de {tracker} enregistrée", tracker=tracker))

    @app.post("/trackers/rule/delete")
    def delete_rule(request: Request, tracker: str = Form(...)):
        with db(request) as session:
            trackers.delete_rule(session, tracker)
        return _back(request, "/trackers", toast=tr("Règle de {tracker} supprimée", tracker=tracker))

    @app.post("/trackers/rename")
    def rename_tracker(request: Request, tracker: str = Form(...), new_name: str = Form(...)):
        with db(request) as session:
            trackers.rename(session, tracker, new_name)
        return _back(request, "/trackers", toast=f"{tracker} → {new_name.strip()}")

    @app.post("/trackers/merge")
    def merge_tracker(request: Request, tracker: str = Form(...), into: str = Form(...), name: str = Form("")):
        with db(request) as session:
            trackers.merge(session, tracker, into)
            if name.strip():
                trackers.rename(session, into, name)
        target = name.strip() or into
        return _back(request, "/trackers", toast=tr("{tracker} regroupé dans {target}", tracker=tracker, target=target))

    @app.post("/trackers/split")
    def split_tracker(request: Request, tracker: str = Form(...)):
        with db(request) as session:
            trackers.split(session, tracker)
        return _back(request, "/trackers", toast=tr("{tracker} séparé en domaines", tracker=tracker))

    @app.get("/rules.yaml", response_class=PlainTextResponse)
    def export_rules(request: Request):
        with db(request) as session:
            data = trackers.export(session)
        return PlainTextResponse(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            headers={"Content-Disposition": "attachment; filename=seedkit-rules.yaml"},
        )

    @app.post("/rules/import")
    async def import_rules(request: Request, file: UploadFile):
        try:
            data = yaml.safe_load(await file.read()) or {}
            with db(request) as session:
                n = trackers.import_(session, data)
        except (yaml.YAMLError, ValueError, TypeError) as exc:
            raise HTTPException(400, tr("Fichier de règles invalide : {error}", error=exc)) from exc
        return _back(request, "/trackers", toast=tr("{n} tracker(s) importé(s)", n=n))

    # Cleanup ----------------------------------------------------------------------

    def cleanup_context(session: Session, ruleset) -> dict:
        evaluation = engine.evaluate(session, ruleset)
        total = analytics.overview(session)["size"]
        groups = {
            kind: views.duplicate_groups(evaluation, kind) for kind in ("episode_in_pack", "same_files", "same_title")
        }
        return {
            "ev": evaluation,
            "total": total,
            "pending": autoclean.pending(session, evaluation),
            "groups": groups,
            "no_hnr_rules": not analytics.Catalog.load(session).rules,
        }

    @app.get("/cleanup", response_class=HTMLResponse)
    def cleanup_page(request: Request):
        _, ruleset, errors = rulesfile.load(settings)
        ctx = {"errors": errors, "rules_path": str(rulesfile.path(settings))}
        if ruleset is not None:
            with db(request) as session:
                ctx |= cleanup_context(session, ruleset)
        return render(request, "cleanup.html", page="cleanup", **ctx)

    @app.post("/cleanup/delete")
    def cleanup_delete(request: Request, hashes: list[str] = Form(...)):
        _, ruleset, _ = rulesfile.load(settings)
        if ruleset is None:
            return _back(request, "/cleanup", toast=tr("Le fichier de règles contient des erreurs."))
        deleted = []
        try:
            with db(request) as session:
                # Only current candidates can be deleted, with the options of the rule that selected them.
                by_rule: dict[str, list[str]] = {}
                rules_by_name = {}
                for m in engine.evaluate(session, ruleset).candidates:
                    if m.torrent.hash in hashes:
                        by_rule.setdefault(m.rule.name, []).append(m.torrent.hash)
                        rules_by_name[m.rule.name] = m.rule
                for name, selected in by_rule.items():
                    rule = rules_by_name[name]
                    deleted += cleanup.delete(service.client, session, settings, selected, rule.delete_files, rule=name)
        except cleanup.DeletionDisabled as exc:
            return _back(request, "/cleanup", toast=str(exc))
        freed = FILTERS["size"](sum(t.size for t in deleted))
        return _back(
            request, "/cleanup", toast=tr("{n} torrent(s) supprimé(s), {size} libérés", n=len(deleted), size=freed)
        )

    @app.get("/cleanup/rules", response_class=HTMLResponse)
    def rules_editor(request: Request):
        text, _, errors = rulesfile.load(settings)
        return render(
            request,
            "rules_editor.html",
            page="cleanup",
            text=text,
            errors=errors,
            fields=engine.field_reference(),
            rules_path=str(rulesfile.path(settings)),
        )

    @app.post("/cleanup/rules/check", response_class=HTMLResponse)
    def rules_check(request: Request, text: str = Form("")):
        try:
            ruleset = parse_rules(text)
        except RuleSetError as exc:
            return render(request, "partials/rules_check.html", errors=exc.errors, ev=None)
        with db(request) as session:
            evaluation = engine.evaluate(session, ruleset)
        return render(request, "partials/rules_check.html", errors=[], ev=evaluation)

    @app.post("/cleanup/rules")
    def rules_save(request: Request, text: str = Form("")):
        try:
            rulesfile.save(settings, text)
        except RuleSetError as exc:
            return render(
                request,
                "rules_editor.html",
                page="cleanup",
                text=text,
                errors=exc.errors,
                fields=engine.field_reference(),
                rules_path=str(rulesfile.path(settings)),
            )
        return RedirectResponse("/cleanup?" + urlencode({"toast": tr("Règles enregistrées")}), status_code=303)

    # Files (SMB) ------------------------------------------------------------------

    @app.get("/files", response_class=HTMLResponse)
    def files_page(request: Request):
        with db(request) as session:
            scan = last_smb_scan(session)
        template = "partials/files_result.html" if is_htmx(request) else "files.html"
        return render(request, template, page="files", scan=scan, check=None)

    @app.post("/files/check", response_class=HTMLResponse)
    def files_check(request: Request):
        if not settings.smb_enabled:
            raise HTTPException(400, tr("SMB non configuré"))
        try:
            check = service.smb_check()
        except Exception as exc:
            check = {"error": f"{type(exc).__name__}: {exc}", "checked": [], "ok": False}
        return render(request, "partials/files_check.html", check=check)

    @app.post("/files/scan")
    def files_scan(request: Request):
        if not settings.smb_enabled:
            raise HTTPException(400, tr("SMB non configuré"))
        service.smb_scan_async()
        return _back(request, "/files", toast=tr("Analyse SMB lancée"))

    # Journal & misc -----------------------------------------------------------------

    @app.get("/journal", response_class=HTMLResponse)
    def journal(request: Request):
        with db(request) as session:
            entries = views.journal(session)
        return render(request, "journal.html", page="journal", entries=entries)

    @app.post("/collect")
    def collect_now(request: Request):
        service.collect()
        if service.last_error:
            return _back(request, toast=tr("Échec : {error}", error=service.last_error))
        return _back(request, toast=tr("Collecte terminée"))

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz(request: Request):
        with db(request) as session:
            session.scalar(select(Torrent.hash).limit(1))
        return "ok"

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    return app
