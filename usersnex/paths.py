# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Dove stanno le cose (come in DroidSnex): da sorgente tutto accanto a gui.py / run.py,
nell'eseguibile i file di lavoro accanto all'exe o in %LOCALAPPDATA%\\UserSnex."""
from __future__ import annotations

import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cartella del progetto
BUNDLE_DIR = getattr(sys, "_MEIPASS", None) or _ROOT
# cartella dell'eseguibile: nella release --onefile i dati (sites/usersnex.yaml, docs/manual, licenze) stanno
# qui accanto a UserSnex.exe, fuori dal bundle, cosi' si leggono e si aggiornano senza ricompilare
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else _ROOT


def data_path(*parts: str) -> str:
    """Un file di dati della release: accanto all'exe se c'e', altrimenti dentro il bundle (o nel progetto)."""
    for root in (APP_DIR, BUNDLE_DIR):
        p = os.path.join(root, *parts)
        if os.path.exists(p):
            return p
    return os.path.join(APP_DIR, *parts)


def _writable(d: str) -> bool:
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".usersnex-write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _home() -> str:
    if os.environ.get("USERSNEX_HOME"):           # cartella di lavoro alternativa (es. tools/make_manual.py)
        return os.environ["USERSNEX_HOME"]
    if not FROZEN:
        return _ROOT
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if _writable(exe_dir):
        return exe_dir
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "UserSnex")


HOME_DIR = _home()
RESULTS_DIR = os.path.join(HOME_DIR, "results")
CACHE_DIR = os.path.join(HOME_DIR, ".usersnex_cache")
CONFIG_FILE = os.path.join(HOME_DIR, "config.json")
LISTS_DIR = os.path.join(HOME_DIR, "lists")        # WhatsMyName, Sherlock, Maigret scaricate (fuori da git)
LISTS_INFO = os.path.join(LISTS_DIR, "info.json")
MYTARGETS_FILE = os.path.join(HOME_DIR, "mytargets.yaml")   # siti aggiunti dall'utente (scheda Config)
MANUAL_DIR = data_path("docs", "manual")   # docs/manual/<lingua>/MANUAL.md + screenshot


def manual_path(lang: str) -> str | None:
    """MANUAL.md nella lingua, altrimenti in inglese, altrimenti in italiano; None se non c'è niente."""
    for code in (lang, "en", "it"):
        p = os.path.join(MANUAL_DIR, code or "", "MANUAL.md")
        if code and os.path.isfile(p):
            return p
    return None
