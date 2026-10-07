"""Command line entry point: python -m radar <command>."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta

from radar import analysis, outcomes
from radar.clock import PHASE_CLOSED, PHASE_POSTMARKET, PHASE_PREMARKET, market_phase, now_et
from radar.config import ROOT, load_config, sec_user_agent
from radar.db import Database
from radar.enrich import Services
from radar.envfile import load_env
from radar.fastwatch import FastWatcher
from radar.notify import send_telegram, telegram_chat_ids, telegram_credentials
from radar.providers.sec import SecClient
from radar.scanner import refresh_reverse_splits, run_cycle

log = logging.getLogger("radar")


def build_services(cfg: dict) -> Services:
    db = Database(cfg["db_path"])
    sec = SecClient(sec_user_agent(cfg), cfg["cache_dir"], cfg["sec"]["min_interval_s"])
    return Services(db=db, sec=sec, cfg=cfg)


def cmd_scan(cfg: dict, args: argparse.Namespace) -> None:
    svc = build_services(cfg)
    now = now_et()
    phase = args.phase or market_phase(now, cfg["schedule"])
    print(json.dumps(run_cycle(svc, now, phase), ensure_ascii=False, indent=2))


def _interval(phase: str, sched: dict) -> int:
    if phase in (PHASE_PREMARKET, PHASE_POSTMARKET):
        return int(sched["premarket_interval_min"]) * 60
    return int(sched["regular_interval_min"]) * 60


def _fast_watch_until(watcher: FastWatcher | None, deadline: float, cfg: dict) -> None:
    """Between full cycles, poll ALTA/MAXIMA tickers for the start of a move."""
    step = float(cfg["fastwatch"]["interval_seconds"])
    while time.time() < deadline - 1:
        if watcher is not None:
            try:
                started = watcher.poll(now_et())
                if started:
                    log.info("ARRANQUE detectado: %s", started)
            except Exception as exc:
                log.warning("Vigilancia rapida fallo: %s", exc)
        time.sleep(max(1.0, min(step, deadline - time.time())))


def cmd_run(cfg: dict, args: argparse.Namespace) -> None:
    """Continuous loop following the market calendar.

    --max-minutes and --exit-when-closed let a cloud job (GitHub Actions, max
    6 h per job) run in slices: it stops on its own before being killed.
    """
    svc = build_services(cfg)
    sched = cfg["schedule"]
    watcher = FastWatcher(svc.db, cfg) if cfg["fastwatch"]["enabled"] else None
    last_outcomes = datetime.min.replace(tzinfo=now_et().tzinfo)
    stop_at = time.time() + args.max_minutes * 60 if args.max_minutes else None
    log.info("Radar en marcha. Ctrl+C para detener.")
    while True:
        now = now_et()
        phase = market_phase(now, sched)
        scan = phase not in (PHASE_CLOSED,) and not (
            phase == PHASE_POSTMARKET and not sched["scan_postmarket"]
        )
        if not scan and args.exit_when_closed:
            log.info("Mercado cerrado (%s): fin de esta tanda", now.strftime("%Y-%m-%d %H:%M ET"))
            break
        if stop_at and time.time() >= stop_at:
            log.info("Tiempo maximo de esta tanda alcanzado")
            break
        if scan:
            try:
                summary = run_cycle(svc, now, phase)
                log.info("Ciclo: %s", summary)
            except Exception as exc:
                log.exception("Ciclo fallido: %s", exc)
        if now - last_outcomes >= timedelta(minutes=int(sched["outcomes_interval_min"])):
            try:
                n = outcomes.update_all(svc.db, now, cfg)
                log.info("Resultados posteriores actualizados: %d", n)
            except Exception as exc:
                log.warning("Actualizacion de resultados fallo: %s", exc)
            last_outcomes = now
        wait = _interval(phase, sched) if scan else 15 * 60
        # Align to the interval grid (e.g. every 5 minutes on the clock).
        deadline = time.time() + max(30, wait - (time.time() % wait))
        if stop_at:
            deadline = min(deadline, stop_at)
        _fast_watch_until(watcher if scan else None, deadline, cfg)
    svc.db.close()


def cmd_once(cfg: dict, args: argparse.Namespace) -> None:
    """One cycle + outcomes update, then exit. For schedulers (GitHub Actions, cron)."""
    svc = build_services(cfg)
    now = now_et()
    phase = market_phase(now, cfg["schedule"])
    if phase == PHASE_CLOSED or (phase == PHASE_POSTMARKET and not cfg["schedule"]["scan_postmarket"]):
        log.info("Mercado cerrado (%s): no se analiza", now.strftime("%Y-%m-%d %H:%M ET"))
    else:
        log.info("Ciclo: %s", run_cycle(svc, now, phase))
    try:
        log.info("Resultados posteriores actualizados: %d", outcomes.update_all(svc.db, now, cfg))
    except Exception as exc:
        log.warning("Actualizacion de resultados fallo: %s", exc)
    svc.db.close()


def cmd_outcomes(cfg: dict, args: argparse.Namespace) -> None:
    db = Database(cfg["db_path"])
    print(f"Alertas actualizadas: {outcomes.update_all(db, now_et(), cfg)}")


def cmd_report(cfg: dict, args: argparse.Namespace) -> None:
    db = Database(cfg["db_path"])
    rep = analysis.report(db.alerts(limit=100000), hit_pct=args.hit)
    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else analysis.format_report(rep))


def cmd_refresh_splits(cfg: dict, args: argparse.Namespace) -> None:
    svc = build_services(cfg)
    refresh_reverse_splits(svc, now_et(), force=True)
    rows = svc.db.rs_filings_since((now_et().date() - timedelta(days=30)).isoformat())
    for r in rows[:40]:
        print(f"{r['filed_date']}  {r['tickers'] or '-':<12} {r['form']:<8} "
              f"{r['ratio_text'] or 'ratio N/D':<18} efectivo {r['effective_date'] or 'N/D'}")


def cmd_dashboard(cfg: dict, args: argparse.Namespace) -> None:
    from radar.dashboard.server import serve
    serve(cfg)


def cmd_telegram_setup(cfg: dict, args: argparse.Namespace) -> None:
    import os
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Falta TELEGRAM_BOT_TOKEN en el archivo .env (ver .env.example).")
        return
    try:
        chats = telegram_chat_ids(token)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 409:
            print("El bot tiene un webhook activo (otra automatizacion lo usa). "
                  "Copia el chat_id de esa automatizacion o crea un bot nuevo.")
        else:
            print(f"No se pudo consultar Telegram (codigo {status}). Revisa el token.")
        return
    if not chats:
        print("No hay mensajes recientes. Escribe cualquier cosa a tu bot en Telegram y repite.")
        return
    print("Chats que han escrito al bot (copia el chat_id a TELEGRAM_CHAT_ID en .env):")
    for c in chats:
        print(f"  chat_id={c['chat_id']}  tipo={c['tipo']}  nombre={c['nombre']}")


def cmd_telegram_test(cfg: dict, args: argparse.Namespace) -> None:
    creds = telegram_credentials()
    if not creds:
        print("Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID en .env.")
        return
    ok = send_telegram("Radar de acciones: prueba de conexion correcta.", *creds)
    print("Mensaje enviado." if ok else "Telegram rechazo el mensaje (revisa token y chat_id).")


COMMANDS = {
    "scan": (cmd_scan, "Ejecuta un ciclo de analisis ahora"),
    "run": (cmd_run, "Bucle continuo (premarket, cada 5 min en mercado)"),
    "once": (cmd_once, "Un ciclo y termina (para ejecucion programada)"),
    "outcomes": (cmd_outcomes, "Actualiza la evolucion posterior de las alertas"),
    "report": (cmd_report, "Estadisticas: que senales han funcionado"),
    "refresh-splits": (cmd_refresh_splits, "Actualiza reverse splits desde la SEC"),
    "dashboard": (cmd_dashboard, "Panel web local"),
    "telegram-setup": (cmd_telegram_setup, "Muestra tu chat_id de Telegram"),
    "telegram-test": (cmd_telegram_test, "Envia un mensaje de prueba"),
}


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser(prog="radar", description="Radar de movimientos especulativos")
    parser.add_argument("--config", default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_, help_text) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        if name == "scan":
            p.add_argument("--phase", choices=["premarket", "regular", "postmarket", "cerrado"])
        if name == "run":
            p.add_argument("--max-minutes", type=float, default=None,
                           help="parar tras N minutos (ejecucion en la nube por tandas)")
            p.add_argument("--exit-when-closed", action="store_true",
                           help="terminar si el mercado esta cerrado")
        if name == "report":
            p.add_argument("--hit", type=float, default=analysis.DEFAULT_HIT_PCT)
            p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    cfg = load_config(args.config)
    COMMANDS[args.command][0](cfg, args)


if __name__ == "__main__":
    main()
