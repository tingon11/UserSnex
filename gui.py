#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Avvio dell'interfaccia grafica:  python gui.py   (oppure: python gui.py mario.rossi88)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from usersnex.gui import main  # noqa: E402
except ImportError as e:
    sys.exit(f"Manca una dipendenza ({e.name}). Installa con:  pip install -r requirements.txt")

raise SystemExit(main())
