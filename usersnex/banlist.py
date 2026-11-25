# UserSnex — siti messi al bando dall'utente.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""banlist.json: i siti che l'utente ha escluso (link fuori uso, risposte sbagliate).

Per ogni sito si salva l'impronta della sua definizione (indirizzi e regole di verifica). Quando le liste
si aggiornano (WhatsMyName, Sherlock, Maigret, UserSnex) o il sito viene modificato in mytargets.yaml:

- definizione uguale  -> il sito resta al bando;
- definizione cambiata -> il ban decade e il sito torna nella lista attiva (forse l'hanno sistemato).

Il file è personale: non va su git.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

from .paths import HOME_DIR
from .sites import SiteDef

BANLIST_FILE = os.path.join(HOME_DIR, "banlist.json")
# i campi che decidono che cosa viene interrogato e come si legge la risposta
_DEF_FIELDS = ("url", "check_url", "type", "require", "absent_text", "present_text", "present_any",
               "missing_status", "found_status", "regex", "strip_chars", "headers", "follow_redirects",
               "dns_missing")


def fingerprint(site: SiteDef) -> str:
    data = {k: getattr(site, k) for k in _DEF_FIELDS}
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load(path: str = BANLIST_FILE) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data.get("siti", {}) if isinstance(data, dict) else {}


def save(entries: dict[str, dict], path: str = BANLIST_FILE) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"siti": entries}, f, indent=1, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)


def ban(sites: list[SiteDef], reason: str = "", path: str = BANLIST_FILE) -> int:
    entries = load(path)
    for s in sites:
        entries[s.name.lower()] = {"name": s.name, "def": fingerprint(s), "source": s.source,
                                   "reason": reason, "date": time.strftime("%Y-%m-%d %H:%M")}
    save(entries, path)
    return len(sites)


def unban(names: list[str], path: str = BANLIST_FILE) -> int:
    entries = load(path)
    n = 0
    for name in names:
        if entries.pop(name.lower(), None) is not None:
            n += 1
    if n:
        save(entries, path)
    return n


def apply(sites: list[SiteDef], path: str = BANLIST_FILE) -> tuple[int, list[str]]:
    """Disattiva i siti al bando con la stessa definizione; toglie dal bando quelli cambiati.
    Ritorna (quanti sono al bando, nomi tornati attivi). I siti spariti dalle liste restano nel file."""
    entries = load(path)
    if not entries:
        return 0, []
    banned, released = 0, []
    for s in sites:
        e = entries.get(s.name.lower())
        if e is None:
            continue
        if e.get("def") == fingerprint(s):
            s.banned = True
            s.enabled = False
            banned += 1
        else:
            released.append(s.name)
            del entries[s.name.lower()]
    if released:
        save(entries, path)
    return banned, released
