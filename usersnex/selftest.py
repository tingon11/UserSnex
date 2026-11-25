# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Autotest dei siti: verifica che ogni definizione funzioni davvero dalla tua rete.

Per ogni sito attivo controlla un account che esiste (campo `known`) e uno inventato:
    ok              trova quello vero, non trova quello inventato
    falso negativo  non trova l'account vero          -> la regola è vecchia
    falso positivo  "trova" anche l'account inventato -> il sito risponde sempre sì
    errore          403/429/anti-bot/rete

I siti che non superano il test finiscono in site_health.json (accanto a gui.py) e vengono
saltati nelle ricerche successive. Rilancia l'autotest per riprovarli.
"""
from __future__ import annotations

import json
import os
import random
import string
import time

from .engine import Engine
from .models import Status
from .paths import HOME_DIR
from .sites import SiteDef

HEALTH_FILE = os.path.join(HOME_DIR, "site_health.json")


def fake_username() -> str:
    rnd = random.Random()
    return "zq" + "".join(rnd.choice(string.ascii_lowercase + string.digits) for _ in range(10))


def run_selftest(sites: list[SiteDef], engine: Engine, on_progress=None, cancel=None) -> dict[str, dict]:
    fake = fake_username()
    pairs, testable = [], []
    for s in sites:
        if engine.active(s) and s.known:
            testable.append(s)
            pairs += [(s, s.known[0]), (s, fake)]
    results = engine.run_pairs(pairs, on_result=on_progress, cancel=cancel)
    by = {(r.site, r.username): r for r in results}
    report: dict[str, dict] = {}
    for s in testable:
        real, bogus = by.get((s.name, s.known[0])), by.get((s.name, fake))
        if real is None or bogus is None:
            continue
        if real.status == Status.ERROR or bogus.status == Status.ERROR:
            verdict, note = "errore", (real.error or bogus.error)
        elif real.status == Status.FOUND and bogus.status == Status.NOT_FOUND:
            verdict, note = "ok", ""
        elif bogus.status == Status.FOUND:
            verdict, note = "falso positivo", f"trova anche {fake}"
        else:
            verdict, note = "falso negativo", f"non trova {s.known[0]}"
        report[s.name] = {"esito": verdict, "nota": note, "http": [real.http_status, bogus.http_status]}
    return report


def save_health(report: dict[str, dict]) -> str:
    data = {"data": time.strftime("%Y-%m-%d %H:%M"), "siti": report}
    with open(HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    return HEALTH_FILE


def load_health() -> dict[str, dict]:
    try:
        with open(HEALTH_FILE, encoding="utf-8") as f:
            return json.load(f).get("siti", {})
    except (OSError, ValueError):
        return {}


def apply_health(sites: list[SiteDef]) -> int:
    """Disattiva i siti che all'ultimo autotest non funzionavano; restituisce quanti."""
    health = load_health()
    n = 0
    for s in sites:
        h = health.get(s.name)
        if h and h["esito"] != "ok" and s.enabled:
            s.enabled = False
            n += 1
    return n
