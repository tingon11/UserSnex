#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Avvio rapido da riga di comando senza installazione:  python run.py <username> [opzioni]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usersnex.cli import main  # noqa: E402

raise SystemExit(main())
