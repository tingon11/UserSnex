# UserSnex — maschera "Nuova risorsa".  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Finestra per aggiungere o modificare un sito in mytargets.yaml, con prova prima di salvare."""
from __future__ import annotations

import threading
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from .. import mytargets
from ..i18n import tr
from . import frameless, theme
from .widgets import button

MODE_LABELS = {
    "status": "Codice HTTP: 200 = esiste, 404 = non esiste",
    "present": "Testo presente solo se l'account esiste",
    "absent": "Testo che compare se l'account non esiste",
    "json": "API JSON: campo presente se l'account esiste",
}
TEXT_HINT = {"status": "", "present": "es. Iscritto dal", "absent": "es. Utente non trovato", "json": "es. data.id"}
VERDICT_COLOR = {"ok": theme.OK, "falso positivo": theme.ERR, "falso negativo": theme.WARN, "errore": theme.ERR}


def open_form(root, fonts: theme.Fonts, categories: list[str], on_saved: Callable[[str], None],
              entry: dict | None = None, from_list: bool = False) -> ctk.CTkToplevel:
    """Apre la maschera. `entry` è la voce da modificare (None = nuova risorsa).
    `from_list`: la voce viene da un'altra lista (Maigret, WhatsMyName...): salvandola, la versione
    modificata va in mytargets.yaml e conserva le regole che la maschera non mostra."""
    editing = entry is not None
    base = dict(entry) if from_list and entry else None
    entry = entry or {}
    win = ctk.CTkToplevel(root, fg_color=theme.BG)
    title = tr("Modifica risorsa") if editing else tr("Nuova risorsa")
    win.title(f"UserSnex · {title}")
    win.resizable(False, False)
    win.transient(root)
    box = ctk.CTkFrame(win, fg_color=theme.PANEL, border_width=1, border_color=theme.BORDER, corner_radius=4)
    box.pack(fill="both", expand=True, padx=10, pady=10)
    box.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(box, text=title.upper(), font=fonts.title, text_color=theme.TEXT_DIM, anchor="w").grid(
        row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(16, 4))
    ctk.CTkLabel(box, text=tr("Il sito viene salvato in mytargets.yaml e ha la precedenza sulle altre liste. "
                              "Usa {username} nell'indirizzo al posto del nome utente."),
                 font=fonts.small, text_color=theme.TEXT_SOFT, anchor="w", justify="left", wraplength=520).grid(
        row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 12))
    r = 2

    def label(text: str) -> None:
        ctk.CTkLabel(box, text=tr(text).upper(), font=fonts.caption, text_color=theme.TEXT_FAINT, anchor="w",
                     width=130).grid(row=r, column=0, sticky="w", padx=(18, 8), pady=4)

    def field(text: str, value: str = "", hint: str = "") -> ctk.CTkEntry:
        nonlocal r
        label(text)
        e = ctk.CTkEntry(box, width=400, height=28, font=fonts.body, placeholder_text=tr(hint) if hint else None)
        if value:
            e.insert(0, value)
        e.grid(row=r, column=1, sticky="ew", padx=(0, 18), pady=4)
        r += 1
        return e

    e_name = field("Nome", entry.get("name", ""), "es. Forum di Esempio")
    label("Categoria")
    # categorie mostrate nella lingua dell'interfaccia, salvate con la chiave italiana del programma
    cat_keys = {tr(c): c for c in sorted(set(categories) | {"varie"})}
    e_cat = ctk.CTkComboBox(box, values=sorted(cat_keys, key=str.lower), width=400, height=28, font=fonts.body,
                            dropdown_font=fonts.body)
    e_cat.set(tr(entry.get("category", "varie")))
    e_cat.grid(row=r, column=1, sticky="ew", padx=(0, 18), pady=4)
    r += 1
    e_url = field("URL del profilo", entry.get("url", ""), "https://esempio.it/utente/{username}")
    check = entry.get("check_url", "")
    e_check = field("URL di verifica", "" if check == entry.get("url") else check,
                    "facoltativo: un'API o una pagina diversa")
    label("Verifica")
    mode0, text0 = mytargets.mode_of(entry) if editing else ("status", "")
    labels = [tr(v) for v in MODE_LABELS.values()]
    by_label = dict(zip(labels, MODE_LABELS))
    e_mode = ctk.CTkOptionMenu(box, values=labels, width=400, height=28, font=fonts.body, dropdown_font=fonts.body)
    e_mode.set(tr(MODE_LABELS[mode0]))
    e_mode.grid(row=r, column=1, sticky="ew", padx=(0, 18), pady=4)
    r += 1
    e_text = field("Testo o campo", text0)
    known = entry.get("known") or [""]
    e_known = field("Account di prova", known[0] if isinstance(known, list) else str(known),
                    "un username che esiste davvero sul sito")

    def mode_changed(_v=None) -> None:
        m = by_label[e_mode.get()]
        e_text.configure(state="normal" if m != "status" else "disabled",
                         placeholder_text=tr(TEXT_HINT[m]) if TEXT_HINT[m] else "")
    e_mode.configure(command=mode_changed)
    mode_changed()

    msg = ctk.CTkLabel(box, text="", font=fonts.body, text_color=theme.TEXT_SOFT, anchor="w", justify="left",
                       wraplength=520)
    msg.grid(row=r, column=0, columnspan=2, sticky="ew", padx=18, pady=(10, 0))
    r += 1
    row = ctk.CTkFrame(box, fg_color="transparent")
    row.grid(row=r, column=0, columnspan=2, sticky="ew", padx=18, pady=(12, 16))
    state = {"tested": None}

    def current() -> dict | None:
        try:
            cat = e_cat.get().strip()
            mode, text = by_label[e_mode.get()], e_text.get()
            same_check = base is not None and (mode, text.strip()) == (mode0, str(text0).strip())
            d = mytargets.build(e_name.get(), cat_keys.get(cat, cat), e_url.get(), e_check.get(),
                                "status" if same_check else mode, "" if same_check else text, e_known.get())
            return mytargets.merge(base, d, keep_check=same_check) if base is not None else d
        except ValueError as e:
            msg.configure(text=tr(str(e)), text_color=theme.ERR)
            return None

    def show_result(res: dict) -> None:
        if not win.winfo_exists():
            return
        btn_test.configure(state="normal")
        v = res.get("esito", "errore")
        http = " / ".join(str(h) for h in res.get("http", []) if h)
        text = tr("Prova: {v}", v=tr(v)) + (f"  (HTTP {http})" if http else "")
        if v == "errore" and res.get("nota"):
            text += f"  ·  {res['nota']}"
        if v == "ok":
            text += "\n" + tr("Funziona: trova l'account di prova e non trova un nome inventato.")
        elif v == "falso positivo":
            text += "\n" + tr("Il sito risponde sì anche a un nome inventato: cambia la verifica (testo o campo).")
        elif v == "falso negativo":
            text += "\n" + tr("Non trova l'account di prova: controlla l'URL e il testo.")
        elif v == "senza account noto":
            text = tr("Indica un account di prova per poter provare il sito.")
        msg.configure(text=text, text_color=VERDICT_COLOR.get(v, theme.TEXT_SOFT))

    def do_test() -> None:
        d = current()
        if d is None:
            return
        btn_test.configure(state="disabled")
        msg.configure(text=tr("prova in corso…"), text_color=theme.TEXT_SOFT)

        def work() -> None:
            try:
                res = mytargets.test(d)
            except Exception as e:  # noqa: BLE001
                res = {"esito": "errore", "nota": str(e), "http": []}
            state["tested"] = res
            try:
                win.after(0, lambda: show_result(res))
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=work, daemon=True).start()

    def close() -> None:
        try:
            win.grab_release()
        except tk.TclError:
            pass
        win.destroy()

    def do_save() -> None:
        d = current()
        if d is None:
            return
        try:
            mytargets.upsert(d, old_name=entry.get("name") if editing else None)
        except OSError as e:
            msg.configure(text=tr("salvataggio non riuscito: {e}", e=e), text_color=theme.ERR)
            return
        close()
        on_saved(d["name"])

    button(row, tr("Salva"), do_save, primary=True).pack(side="right")
    button(row, tr("Annulla"), close).pack(side="right", padx=(0, 8))
    btn_test = button(row, "▶  " + tr("Prova"), do_test)
    btn_test.pack(side="left")
    win.protocol("WM_DELETE_WINDOW", close)
    win.bind("<Escape>", lambda _e: close())
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = root.winfo_rootx() + (root.winfo_width() - w) // 2
    y = root.winfo_rooty() + (root.winfo_height() - h) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    frameless.style_titlebar(win, theme.BG, theme.TEXT_SOFT, theme.BORDER)
    win.after(50, lambda: (win.lift(), e_name.focus_force(), win.grab_set()) if win.winfo_exists() else None)
    return win
