# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Riga di comando:  python run.py <username> [opzioni]"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from . import __version__
from .correlate import correlate
from .engine import DiskCache, Engine
from .export import export_html, export_json_csv
from .models import Profile, SiteResult, Status
from .paths import CACHE_DIR, RESULTS_DIR
from . import banlist
from .selftest import apply_health, run_selftest, save_health
from . import updater
from .sites import load_sites, upstream_missing

try:
    from colorama import Fore, Style, just_fix_windows_console
    just_fix_windows_console()
    G, R, Y, B, D, X = Fore.GREEN, Fore.RED, Fore.YELLOW, Fore.BLUE, Style.DIM, Style.RESET_ALL
except ImportError:  # senza colorama: niente colori
    G = R = Y = B = D = X = ""


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="usersnex", description="UserSnex: ricerca username con correlazione d'identità")
    ap.add_argument("username", nargs="?", help="username da cercare")
    ap.add_argument("--selftest", action="store_true",
                    help="verifica i siti con account noti e disattiva quelli che non funzionano")
    ap.add_argument("--extended", action="store_true",
                    help="anche la lista estesa (~10.000 forum e siti minori): molto più lenta")
    ap.add_argument("--include-broken", action="store_true", help="usa anche i siti bocciati dall'autotest")
    ap.add_argument("--name", help="nome noto della persona (indizio)")
    ap.add_argument("--location", help="località nota (indizio)")
    ap.add_argument("--link", action="append", default=[], help="link noto (sito personale, altro profilo); ripetibile")
    ap.add_argument("--only", help="solo questi siti, separati da virgola")
    ap.add_argument("--sites", action="append", default=[], help="file o cartella con definizioni YAML aggiuntive")
    ap.add_argument("--update-lists", action="store_true",
                    help="scarica le liste aggiornate (WhatsMyName, Sherlock, Maigret, UserSnex)")
    ap.add_argument("--usersnex-url", help="indirizzo raw di usersnex.yaml per --update-lists")
    ap.add_argument("--out", default=RESULTS_DIR, help="cartella per JSON / CSV / HTML")
    ap.add_argument("--html", action="store_true", help="crea anche il report HTML")
    ap.add_argument("--no-export", action="store_true", help="non salvare file")
    ap.add_argument("--no-cache", action="store_true", help="non usare la cache delle risposte")
    ap.add_argument("--all", action="store_true", help="mostra anche i siti dove non è stato trovato")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--version", action="version", version=f"UserSnex {__version__}")
    return ap


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):        # console Windows (cp1252) o output rediretto:
        try:                                       # niente crash sui nomi di siti non latini
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    if a.update_lists or upstream_missing():
        if not a.update_lists:
            print(f"{Y}[*]{X} liste di siti non ancora scaricate: le scarico ora")
        updater.update(log=lambda m, _k="info": print(f"{D}[*]{X} {m}"), usersnex_url=a.usersnex_url or updater.DEFAULT_USERSNEX_URL)
        if a.update_lists and not a.username and not a.selftest:
            return 0
    sites = load_sites([Path(p) for p in a.sites])
    n_banned, released = banlist.apply(sites)
    if released:
        print(f"{G}[*]{X} tornati attivi (definizione cambiata dopo il ban): {', '.join(released)}")
    if a.only:
        wanted = {s.strip().lower() for s in a.only.split(",")}
        sites = [s for s in sites if s.name.lower() in wanted]
    if not sites:
        print(f"{R}[!]{X} nessun sito da controllare")
        return 2
    cache = None if a.no_cache else DiskCache(Path(CACHE_DIR))
    lock = threading.Lock()
    if a.selftest:
        return selftest(sites, Engine(sites, timeout=a.timeout, extended=a.extended), lock)
    if not a.username:
        print(f"{R}[!]{X} indica un username (oppure --selftest)")
        return 2
    skipped = 0 if a.include_broken else apply_health(sites)
    engine = Engine(sites, timeout=a.timeout, cache=cache, extended=a.extended)
    active = sum(engine.active(s) for s in sites)

    extra = f"  {D}({skipped} saltati dall'autotest){X}" if skipped else ""
    if n_banned:
        extra += f"  {D}({n_banned} al bando){X}"
    print(f"{B}[*]{X} UserSnex {__version__}: cerco {B}{a.username}{X} su {active} siti{extra}\n")

    def show(r: SiteResult) -> None:
        with lock:
            if r.status == Status.FOUND:
                name = f"  {D}{r.profile.display_name}{X}" if r.profile.display_name else ""
                print(f"{G}[+]{X} {r.site}: {r.url}{name}")
            elif r.status == Status.ERROR:
                print(f"{Y}[!]{X} {r.site}: {D}{r.error}{X}")
            elif a.all:
                print(f"{R}[-]{X} {r.site}: {D}non trovato{X}")

    try:
        results = engine.run(a.username, on_result=show)
    except KeyboardInterrupt:
        print(f"\n{Y}[!]{X} interrotto")
        return 130

    seed = Profile(display_name=a.name or "", location=a.location or "", links=a.link)
    rep = correlate(results, seed)
    found = [r for r in results if r.status == Status.FOUND]
    print(f"\n{B}[*]{X} trovati {len(found)} su {len(results)}  ·  unicità username "
          f"{int(rep.username_uniqueness * 100)}%")
    if rep.clusters:
        print(f"{B}[*]{X} identità principale: {G}{len(rep.clusters[0])} account{X}")
    elif len(found) > 1:
        print(f"{Y}[*]{X} nessun legame tra gli account: probabilmente persone diverse con lo stesso username")
    for acc in rep.accounts:
        color = G if acc.confidence >= 70 else Y if acc.confidence >= 40 else D
        grp = f"#{acc.cluster + 1}" if acc.cluster >= 0 else "  "
        reasons = ", ".join(dict.fromkeys(e.signal for e in acc.evidence[1:]))[:70] or "solo username uguale"
        print(f"    {color}{acc.confidence:3d}%{X}  {grp:>3}  {acc.site:<26} {D}{reasons}{X}")

    if not a.no_export:
        files = export_json_csv(results, rep, a.out)
        if a.html:
            files.append(export_html(results, rep, a.out))
        print(f"\n{B}[*]{X} salvati: " + ", ".join(os.path.relpath(f) for f in files))
    return 0


def selftest(sites, engine, lock) -> int:
    testable = [s for s in sites if engine.active(s) and s.known]
    print(f"{B}[*]{X} autotest di {len(testable)} siti (un account vero e uno inventato ciascuno)\n")
    done = {"n": 0}

    def tick(_r) -> None:
        with lock:
            done["n"] += 1
            if done["n"] % 50 == 0:
                print(f"    {D}{done['n']} / {2 * len(testable)} richieste{X}")

    try:
        report = run_selftest(sites, engine, on_progress=tick)
    except KeyboardInterrupt:
        print(f"\n{Y}[!]{X} interrotto")
        return 130
    colors = {"ok": G, "errore": Y, "falso negativo": R, "falso positivo": R}
    counts: dict[str, int] = {}
    for name, h in sorted(report.items(), key=lambda kv: (kv[1]["esito"] == "ok", kv[0].lower())):
        counts[h["esito"]] = counts.get(h["esito"], 0) + 1
        if h["esito"] != "ok":
            print(f"{colors[h['esito']]}[{h['esito']}]{X} {name}  {D}{h['nota']}  http {h['http']}{X}")
    print(f"\n{B}[*]{X} " + "  ·  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    print(f"{B}[*]{X} salvato {os.path.relpath(save_health(report))}: i siti non ok verranno saltati")
    return 0


if __name__ == "__main__":
    sys.exit(main())
