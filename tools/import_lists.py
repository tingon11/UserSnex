#!/usr/bin/env python3
# UserSnex — importa le liste pubbliche di siti.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Aggiorna le liste WhatsMyName, Sherlock e Maigret da riga di comando (come la scheda Config).

    python tools/import_lists.py                       # scarica da GitHub
    python tools/import_lists.py --wmn wmn-data.json --sherlock data.json   # da file locali
    python tools/import_lists.py --force               # riscrive anche se non è cambiato nulla

La conversione sta in usersnex/updater.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usersnex import updater  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wmn", help="wmn-data.json locale")
    ap.add_argument("--sherlock", help="data.json di Sherlock locale")
    ap.add_argument("--maigret", help="data.json di Maigret locale")
    ap.add_argument("--force", action="store_true", help="riscrive le liste anche se invariate")
    a = ap.parse_args()
    local = {k: v for k, v in (("wmn", a.wmn), ("sherlock", a.sherlock), ("maigret", a.maigret)) if v}
    res = updater.update(log=lambda m, _k="info": print(m), local=local, force=a.force)
    return 1 if any(r.get("esito") == "errore" for r in res.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
