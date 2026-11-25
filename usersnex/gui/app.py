# UserSnex — interfaccia grafica.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Finestra principale di UserSnex, con l'impostazione di DroidSnex / AndrOsint.

Intestazione (logo, username da cercare, stato) che fa da barra del titolo, colonna dei comandi a
sinistra, al centro contatori e schede Risultati / Identità / Grafo / Non trovati / Errori / Siti,
a destra Dettagli e Log, piede con lo stato.

    python gui.py
    python gui.py mario.rossi88        # avvia subito la ricerca
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from types import SimpleNamespace

import customtkinter as ctk

from .. import __version__
from ..correlate import IdentityReport, correlate
from ..engine import DiskCache, Engine
from ..export import export_html, export_json_csv
from ..i18n import LANGUAGES, language, set_language, tr
from ..models import Profile, SiteResult, Status
from ..paths import CACHE_DIR, CONFIG_FILE, LISTS_DIR, MANUAL_DIR, MYTARGETS_FILE, RESULTS_DIR, manual_path
from ..selftest import apply_health, load_health, run_selftest, save_health
from ..sites import SiteDef, list_files, load_sites, to_entry, upstream_missing
from .. import avatars, banlist, mytargets, updater
from .. import terms
from . import about, frameless, manual, resource_form, theme, welcome
from .graphview import GraphView
from .widgets import Card, StatCard, Table, TabBar, button, caption, confirm, rule, window_buttons

APP_NAME = "UserSnex"
CREDITS = "Andrea Cumini - andrea@osintinfo.net - www.osintinfo.net"
DEFAULT_SIZE = "1280x800"
MIN_SIZE = (1040, 660)
SIDE_WIDTH = 228
DETAILS_WIDTH = 290
TABS = (("results", "Risultati"), ("identity", "Identità"), ("graph", "Grafo"),
        ("missing", "Non trovati"), ("errors", "Errori"), ("sites", "Siti"), ("config", "Config"))
HELP = ("Seleziona una riga per vedere il profilo, i link trovati e gli indizi che legano "
        "l'account agli altri.\n\nDoppio clic apre il profilo nel browser.")


def _conf_tag(c: int) -> str:
    return "high" if c >= 70 else "mid" if c >= 40 else "low"


class App(ctk.CTk):
    def __init__(self, username: str = "") -> None:
        theme.apply()
        super().__init__()
        self.title(APP_NAME)
        self.minsize(*MIN_SIZE)
        self.geometry(DEFAULT_SIZE)
        theme.set_icon(self)
        self.frameless = frameless.make_frameless(self, border_color=theme.BORDER)
        self.cfg = self._load_cfg()
        set_language(self.cfg.get("language") or "it")
        # prima apertura (o termini nuovi): lingua e termini d'uso, prima di costruire l'interfaccia
        need_lang = not self.cfg.get("language")
        if need_lang or self.cfg.get("terms_accepted") != terms.TERMS_VERSION:
            self.withdraw()
            self._welcome = SimpleNamespace(language=self.cfg.get("language", ""), terms_accepted="",
                                            terms_accepted_at="")
            welcome.run(self, self._welcome, need_lang, on_accept=lambda: self._after_welcome(username),
                        on_reject=self.destroy)
            return
        self._init_rest(username)

    def _after_welcome(self, username: str) -> None:
        """Termini accettati: salva lingua e accettazione, mostra la finestra e completa l'avvio."""
        w = self._welcome
        self.cfg.update(language=w.language or "it", terms_accepted=w.terms_accepted,
                        terms_accepted_at=w.terms_accepted_at)
        self._save_cfg()
        set_language(self.cfg["language"])
        self.deiconify()
        self._init_rest(username)

    def _init_rest(self, username: str) -> None:
        self.fonts = theme.Fonts()
        theme.style_tables(self)
        self.report_callback_exception = self._tk_exception

        self.ui_queue: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.sites: list[SiteDef] = []
        self.results: dict[str, SiteResult] = {}
        self.report: IdentityReport | None = None
        self.searched = ""
        self.selected: str | None = None
        self.tab = ""
        self._t0 = 0.0
        self.avatar_thumbs: dict[str, str] = {}
        self._avatar_cache: dict[tuple, object] = {}
        self._load_sites()

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-f>", lambda _e: self.filter.focus_set())
        self.bind("<F1>", lambda _e: self._open_manual())
        self.after(100, self._tick)
        self.log(self.site_breakdown())
        missing = upstream_missing()
        if missing:                             # prima esecuzione: le liste esterne non sono nel repository
            self.log(tr("liste di siti non ancora scaricate: le scarico ora (scheda Config)"), "warn")
            self.after(600, lambda: (self._show_tab("config"), self._update_lists()))
            username = ""
        if username:
            self.user_entry.insert(0, username)
            self.after(300, self._toggle_search)
        else:
            self.after(200, self.user_entry.focus_set)

    # ================================================================ impostazioni
    def _load_cfg(self) -> dict:
        cfg = {"use_cache": True}
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
        return cfg

    def _save_cfg(self) -> None:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=1)
        except OSError:
            pass

    def _load_sites(self) -> None:
        try:
            self.sites = load_sites()
        except Exception as e:  # noqa: BLE001 - un YAML rotto (es. mytargets.yaml) non deve impedire l'avvio
            files = [f for f in list_files() if os.path.abspath(f) != os.path.abspath(MYTARGETS_FILE)]
            self.sites = load_sites(files=files)
            msg = tr("mytargets.yaml ignorato: {e}", e=e)
            self.after(500, lambda: self.log(msg, "err"))
        self.n_banned, released = banlist.apply(self.sites)
        if released:
            msg = tr("tornati attivi perché la definizione è cambiata: {s}", s=", ".join(released[:12]) +
                     ("…" if len(released) > 12 else ""))
            self.after(600, lambda: self.log(msg, "ok"))
        self.skipped = apply_health(self.sites)

    @property
    def active_sites(self) -> int:
        ext = bool(self.cfg.get("extended"))
        return sum(s.enabled and (ext or not s.extended) for s in self.sites)

    def site_breakdown(self) -> str:
        """Perché non tutti i siti vengono interrogati: i conti devono tornare con il totale."""
        ext = bool(self.cfg.get("extended"))
        health = load_health()
        off_test = off_prot = off_ext = off_ban = 0
        for s in self.sites:
            if s.banned:
                off_ban += 1
            elif not s.enabled:
                h = health.get(s.name)
                if h and h["esito"] != "ok":
                    off_test += 1
                else:
                    off_prot += 1
            elif s.extended and not ext:
                off_ext += 1
        parts = [tr("{n} siti in tutto", n=len(self.sites)), tr("{a} interrogati", a=self.active_sites)]
        if off_ban:
            parts.append(tr("{k} al bando", k=off_ban))
        if off_test:
            parts.append(tr("{k} esclusi dall'autotest (non affidabili)", k=off_test))
        if off_prot:
            parts.append(tr("{k} disattivati (protezione anti-bot o login)", k=off_prot))
        if off_ext:
            parts.append(tr("{k} nella lista estesa (spenta)", k=off_ext))
        return "  ·  ".join(parts)

    # ================================================================ costruzione
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_header()
        rule(self, True).grid(row=1, column=0, sticky="ew")

        body = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(2, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self._build_side(body)
        rule(body, False).grid(row=0, column=1, sticky="ns")

        main = ctk.CTkFrame(body, fg_color=theme.BG, corner_radius=0)
        main.grid(row=0, column=2, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(3, weight=1)

        head = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
        head.grid(row=0, column=0, sticky="ew", padx=22, pady=(14, 0))
        ctk.CTkLabel(head, text="USERSNEX / " + tr("Ricerca").upper(), anchor="w", font=self.fonts.caption,
                     text_color=theme.TEXT_FAINT, height=14).pack(anchor="w")
        self.page_title = ctk.CTkLabel(head, text=tr("Ricerca username"), anchor="w", font=self.fonts.page,
                                       text_color=theme.TEXT)
        self.page_title.pack(anchor="w")
        self.hint_label = ctk.CTkLabel(main, text="", font=self.fonts.small, text_color=theme.WARN,
                                       anchor="w", justify="left")
        self.hint_label.grid(row=1, column=0, sticky="ew", padx=22, pady=(6, 0))
        self.hint_label.grid_remove()

        stats = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
        stats.grid(row=2, column=0, sticky="ew", padx=14, pady=(10, 0))
        self.c_checked = StatCard(stats, tr("Siti controllati"), self.fonts)
        self.c_found = StatCard(stats, tr("Trovati"), self.fonts)
        self.c_identity = StatCard(stats, tr("Identità principale"), self.fonts)
        self.c_unique = StatCard(stats, tr("Unicità username"), self.fonts)
        self.c_errors = StatCard(stats, tr("Errori"), self.fonts)
        self.c_found.value.configure(text_color=theme.OK)
        self.c_identity.value.configure(text_color=theme.ACCENT)
        self.c_errors.value.configure(text_color=theme.ERR)
        for i, c in enumerate((self.c_checked, self.c_found, self.c_identity, self.c_unique, self.c_errors)):
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else theme.GAP, 0))
            stats.grid_columnconfigure(i, weight=1, uniform="stat")
        self.c_unique.set("—")

        content = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
        content.grid(row=3, column=0, sticky="nsew", padx=14, pady=(theme.GAP, 12))
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(2, weight=1)
        self._build_tabs(content)
        self._build_details(content)
        self._show_tab("results")

        rule(self, True).grid(row=3, column=0, sticky="ew")
        self._build_footer()

    def _build_header(self) -> None:
        """Intestazione: logo, username, stato. Fa anche da barra del titolo."""
        header = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0, height=50)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(2, weight=1)
        header.grid_rowconfigure(0, weight=1)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=(18, 0))
        drag = [header, brand]
        logo = theme.logo(17)
        if logo is not None:
            name = ctk.CTkLabel(brand, text="", image=logo)
            name.pack(side="left")
            drag.append(name)
        else:                       # senza logo.png (tools/make_logo.py): scritta bicolore
            for text, color in (("User", theme.TEXT), ("Snex", theme.ACCENT)):
                lab = ctk.CTkLabel(brand, text=text, font=(theme.mono_font(), 17, "bold"), text_color=color)
                lab.pack(side="left")
                drag.append(lab)
        version = ctk.CTkLabel(brand, text=f"   v{__version__}", font=(theme.mono_font(), 10),
                               text_color=theme.TEXT_DIM)
        version.pack(side="left", pady=(3, 0))
        subtitle = ctk.CTkLabel(brand, text="  ·  username osint", font=(theme.mono_font(), 10),
                                text_color=theme.TEXT_FAINT)
        subtitle.pack(side="left", pady=(3, 0))
        drag += [version, subtitle]

        chip = ctk.CTkFrame(header, fg_color=theme.PANEL, corner_radius=4, border_width=1,
                            border_color=theme.BORDER, height=32)
        chip.grid(row=0, column=1, sticky="w", padx=(20, 0))
        tag = ctk.CTkLabel(chip, text=tr("USERNAME"), font=self.fonts.caption, text_color=theme.TEXT_FAINT)
        tag.pack(side="left", padx=(10, 6), pady=2)
        self.user_entry = ctk.CTkEntry(chip, width=230, height=24, border_width=0, fg_color=theme.PANEL,
                                       font=self.fonts.bold, placeholder_text=tr("es. mario.rossi88"))
        self.user_entry.pack(side="left", pady=3)
        self.user_entry.bind("<Return>", lambda _e: self._toggle_search())
        ctk.CTkButton(chip, text="⌕", width=28, height=24, corner_radius=4, fg_color="transparent",
                      hover_color=theme.HOVER, text_color=theme.TEXT_DIM, font=(theme.ui_font(), 15),
                      command=self._toggle_search).pack(side="left", padx=(2, 4))

        spacer = ctk.CTkFrame(header, fg_color="transparent", height=1)
        spacer.grid(row=0, column=2, sticky="ew")

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.grid(row=0, column=3, sticky="e", padx=(0, 12))
        self.btn_manual = ctk.CTkButton(right, text="📖  " + tr("Manuale"), width=0, height=26, corner_radius=4,
                                        fg_color="transparent", border_width=1, border_color=theme.BORDER_STRONG,
                                        hover_color=theme.HOVER, text_color=theme.ACCENT, font=self.fonts.bold,
                                        command=self._open_manual)
        self.btn_manual.pack(side="left", padx=(0, 18))
        self.state_dot = ctk.CTkLabel(right, text="●", font=(theme.mono_font(), 12), text_color=theme.TEXT_FAINT)
        self.state_dot.pack(side="left", padx=(0, 6))
        self.state_label = ctk.CTkLabel(right, text=tr("Pronto"), font=self.fonts.bold, text_color=theme.TEXT_SOFT)
        self.state_label.pack(side="left")
        drag += [spacer, right, tag, self.state_dot, self.state_label]

        if self.frameless:
            window_buttons(self, header, self._on_close).grid(row=0, column=4, sticky="ne", padx=(4, 0))
            for w in drag:
                frameless.enable_drag(self, w)

    def _build_side(self, body) -> None:
        side = ctk.CTkFrame(body, fg_color=theme.PANEL, corner_radius=0, width=SIDE_WIDTH)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        r = 0

        def section(text, top=12):
            nonlocal r
            caption(side, tr(text), self.fonts).grid(row=r, column=0, sticky="ew", padx=20, pady=(top, 8))
            r += 1

        def place(widget, pady=3):
            nonlocal r
            widget.grid(row=r, column=0, sticky="ew", padx=14, pady=pady)
            r += 1

        section("Ricerca")
        self.btn_search = button(side, "▶  " + tr("Avvia ricerca"), self._toggle_search, primary=True, height=38)
        place(self.btn_search)
        self.btn_selftest = button(side, tr("Autotest siti"), self._selftest, height=30)
        place(self.btn_selftest)

        section("Indizi noti")
        self.seed_entries = {}
        for key, label, ph in (("name", "Nome", "es. Mario Rossi"), ("location", "Località", "es. Milano"),
                               ("link", "Link", "sito o profilo noto")):
            row = ctk.CTkFrame(side, fg_color="transparent")
            ctk.CTkLabel(row, text=tr(label).upper(), font=self.fonts.caption, text_color=theme.TEXT_FAINT,
                         width=58, anchor="w").pack(side="left")
            ent = ctk.CTkEntry(row, height=26, font=self.fonts.body, placeholder_text=tr(ph))
            ent.pack(side="left", fill="x", expand=True)
            ent.bind("<Return>", lambda _e: self._recorrelate())
            ent.bind("<FocusOut>", lambda _e: self._recorrelate())
            self.seed_entries[key] = ent
            place(row, pady=2)

        section("Dati")
        place(button(side, tr("Esporta JSON · CSV"), self._export))
        place(button(side, "▤  " + tr("Report HTML"), self._report))
        place(button(side, tr("Apri cartella export"), lambda: self._open_path(RESULTS_DIR)))

        section("Opzioni")
        cache_row = ctk.CTkFrame(side, fg_color="transparent")
        self.sw_cache = ctk.CTkSwitch(cache_row, text=tr("Usa la cache (1 ora)"), font=self.fonts.body,
                                      command=self._opt_changed)
        self.sw_cache.pack(side="left")
        ctk.CTkButton(cache_row, text=tr("svuota"), width=0, height=18, corner_radius=3, fg_color="transparent",
                      border_width=1, border_color=theme.BORDER_STRONG, hover_color=theme.HOVER,
                      text_color=theme.ACCENT, font=self.fonts.small, command=self._clear_cache).pack(side="right")
        if self.cfg.get("use_cache", True):
            self.sw_cache.select()
        place(cache_row, pady=4)
        self.sw_ext = ctk.CTkSwitch(side, text=tr("Lista estesa (~10.000)"), font=self.fonts.body,
                                    command=self._opt_changed)
        if self.cfg.get("extended"):
            self.sw_ext.select()
        place(self.sw_ext, pady=4)
        lang_row = ctk.CTkFrame(side, fg_color="transparent")
        ctk.CTkLabel(lang_row, text=tr("Lingua").upper(), font=self.fonts.caption, text_color=theme.TEXT_FAINT,
                     width=58, anchor="w").pack(side="left")
        self.lang_menu = ctk.CTkOptionMenu(lang_row, values=list(LANGUAGES.values()), height=26, font=self.fonts.body,
                                           dropdown_font=self.fonts.body, command=self._set_language)
        self.lang_menu.set(LANGUAGES[language()])
        self.lang_menu.pack(side="left", fill="x", expand=True)
        place(lang_row, pady=4)

        section("Stato")
        box = ctk.CTkFrame(side, fg_color=theme.PANEL_2, corner_radius=4, border_width=1, border_color=theme.BORDER)
        place(box)
        box.grid_columnconfigure(2, weight=1)
        self.status_rows = {}
        keys = ("SITI", "RICERCA", "TEMPO")
        for i, key in enumerate(keys):
            dot = ctk.CTkLabel(box, text="●", font=(theme.mono_font(), 11), text_color=theme.TEXT_FAINT, width=12)
            dot.grid(row=i, column=0, padx=(10, 6), pady=(8 if i == 0 else 2, 8 if i == len(keys) - 1 else 2))
            ctk.CTkLabel(box, text=tr(key.capitalize()).upper(), font=self.fonts.caption, text_color=theme.TEXT_FAINT, width=58,
                         anchor="w").grid(row=i, column=1, sticky="w")
            val = ctk.CTkLabel(box, text="—", font=self.fonts.small, text_color=theme.TEXT_SOFT, anchor="w")
            val.grid(row=i, column=2, sticky="ew", padx=(0, 8))
            self.status_rows[key] = (dot, val)
        self._status("SITI", tr("{a} attivi su {n}", a=self.active_sites, n=len(self.sites)), theme.OK)
        self._status("RICERCA", tr("ferma"))
        side.grid_rowconfigure(r, weight=1)
        r += 1
        ctk.CTkLabel(side, text=tr("solo dati pubblici · nessun login"), font=self.fonts.tiny,
                     text_color=theme.TEXT_FAINT).grid(row=r, column=0, sticky="w", padx=20, pady=12)

    def _build_tabs(self, parent) -> None:
        bar = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)
        self.tabbar = TabBar(bar, self.fonts, [(k, tr(t)) for k, t in TABS], self._show_tab)
        self.tabbar.grid(row=0, column=0, sticky="w")
        self.filter = ctk.CTkEntry(bar, width=220, height=30, font=self.fonts.body,
                                   placeholder_text=tr("Filtra sito, nome, link  (Ctrl+F)"))
        self.filter.grid(row=0, column=2, sticky="e")

        compact = {"on": False}

        def fit_filter(e) -> None:          # con schede lunghe (alcune lingue) il filtro si stringe invece di sparire
            room = e.width - self.tabbar.winfo_reqwidth() - 12
            if room < 110 and not compact["on"]:
                compact["on"] = True        # prima si stringono i margini delle schede
                self.tabbar.set_compact(True)
                self.tabbar.update_idletasks()
                room = e.width - self.tabbar.winfo_reqwidth() - 12
            want = max(110, min(220, room))
            if int(self.filter.cget("width")) != want:
                self.filter.configure(width=want)
        bar.bind("<Configure>", fit_filter, add="+")
        self.filter.bind("<KeyRelease>", lambda _e: (self._refill_tables(), self._fill_sites()))

        tags = {"high": theme.OK, "mid": theme.WARN, "low": theme.TEXT_SOFT, "err": theme.ERR,
                "dim": theme.TEXT_DIM}
        self.pages: dict[str, ctk.CTkFrame] = {}

        def page(key) -> ctk.CTkFrame:
            f = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
            f.grid_columnconfigure(0, weight=1)
            f.grid_rowconfigure(0, weight=1)
            self.pages[key] = f
            return f

        self.t_results = Table(page("results"), [
            ("conf", "Conf.", 64, False, "e"), ("site", "Sito", 150, False, "w"),
            ("cat", "Categoria", 96, False, "w"), ("name", "Nome", 150, True, "w"),
            ("loc", "Località", 110, True, "w"), ("url", "Profilo", 250, True, "w"),
            ("grp", "Gruppo", 64, False, "center"), ("ms", "ms", 56, False, "e")], tags, images=True)
        self.t_identity = Table(page("identity"), [
            ("grp", "Gruppo", 70, False, "center"), ("site", "Sito", 150, False, "w"),
            ("conf", "Conf.", 64, False, "e"), ("signals", "Indizi", 420, True, "w")], tags)
        graph_page = page("graph")
        graph_box = ctk.CTkFrame(graph_page, fg_color=theme.PANEL, border_width=1, border_color=theme.BORDER,
                                 corner_radius=4)
        graph_box.grid(row=0, column=0, sticky="nsew")
        self.graph = GraphView(graph_box, self.fonts, on_select=self._select, on_open=self._open_site)
        self.graph.pack(fill="both", expand=True, padx=1, pady=1)
        self.t_missing = Table(page("missing"), [
            ("site", "Sito", 170, False, "w"), ("cat", "Categoria", 110, False, "w"),
            ("url", "Profilo", 300, True, "w"), ("note", "Nota", 200, True, "w")], tags)
        self.t_errors = Table(page("errors"), [
            ("site", "Sito", 170, False, "w"), ("http", "HTTP", 60, False, "center"),
            ("err", "Errore", 300, True, "w"), ("url", "Profilo", 260, True, "w")], tags, selectmode="extended")
        sites_page = page("sites")
        sites_page.grid_rowconfigure(0, weight=0)
        sites_page.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(sites_page, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.btn_ban = button(bar, "⦸  " + tr("Metti al bando"), lambda: self._ban_sites(self.t_sites.tree),
                              danger=True)
        self.btn_ban.pack(side="left")
        self.btn_unban = button(bar, tr("Togli dal bando"), lambda: self._unban_sites(self.t_sites.tree))
        self.btn_unban.pack(side="left", padx=(8, 0))
        self.btn_edit_site = button(bar, "✎  " + tr("Modifica sito"), lambda: self._edit_site(self.t_sites.tree))
        self.btn_edit_site.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(bar, text=tr("al bando: esclusi dalle ricerche finché la loro definizione non cambia; "
                                  "le modifiche vanno in mytargets.yaml"),
                     font=self.fonts.small, text_color=theme.TEXT_DIM, anchor="w", justify="left",
                     wraplength=380).pack(side="left", padx=(12, 0))
        self.t_sites = Table(sites_page, [
            ("site", "Sito", 170, False, "w"), ("cat", "Categoria", 100, False, "w"),
            ("state", "Stato", 150, False, "w"), ("src", "Fonte", 150, False, "w"),
            ("check", "Interrogato", 360, True, "w")], tags, selectmode="extended")
        self._site_menu = self._make_site_menu()
        for t in (self.t_results, self.t_identity, self.t_missing, self.t_errors, self.t_sites):
            t.grid(row=1 if t is self.t_sites else 0, column=0, sticky="nsew")
            t.tree.bind("<<TreeviewSelect>>", lambda _e, tv=t.tree: self._on_tree_select(tv))
            t.tree.bind("<Double-Button-1>", lambda _e, tv=t.tree: self._open_site(self._tree_site(tv)))
            t.tree.bind("<Button-3>", lambda e, tv=t.tree: self._site_popup(e, tv))
        self._fill_sites()
        self._build_config(page("config"))

    # ================================================================ config: aggiornamento liste
    def _build_config(self, page) -> None:
        page.grid_rowconfigure(0, weight=0)
        page.grid_rowconfigure(1, weight=0)
        page.grid_rowconfigure(2, weight=1)
        opt = Card(page, tr("Opzioni"), self.fonts)
        opt.grid(row=0, column=0, sticky="new", pady=(0, theme.GAP))
        self.sw_avatars = ctk.CTkSwitch(opt.body, text=tr("Scarica gli avatar degli account trovati: miniature nella "
                                                          "lista e nel grafo, confronto delle immagini"),
                                        font=self.fonts.body, command=self._avatars_changed)
        if self.cfg.get("avatars", True):
            self.sw_avatars.select()
        self.sw_avatars.pack(anchor="w")
        card = Card(page, tr("Liste di ricerca"), self.fonts)
        card.grid(row=1, column=0, sticky="new")
        body = card.body
        body.grid_columnconfigure(5, weight=1)
        ctk.CTkLabel(body, text=tr("WhatsMyName, Sherlock e Maigret si scaricano dai loro repository, ognuna nel "
                                   "suo file con la sua licenza; usersnex.yaml contiene i siti trovati da "
                                   "UserSnex. L'aggiornamento le scarica, le converte e ricarica i siti."),
                     font=self.fonts.body, text_color=theme.TEXT_SOFT, anchor="w", justify="left",
                     wraplength=760).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 8))
        heads = ("Lista", "Licenza", "Siti", "Aggiornata", "Esito")
        widths = (130, 120, 80, 150, 280)
        for c, (h, w) in enumerate(zip(heads, widths)):
            ctk.CTkLabel(body, text=tr(h).upper(), font=self.fonts.caption, text_color=theme.TEXT_FAINT,
                         width=w, anchor="w").grid(row=1, column=c, sticky="w")
        rule(body, True).grid(row=2, column=0, columnspan=6, sticky="ew", pady=(2, 4))
        self.list_rows: dict[str, list[ctk.CTkLabel]] = {}
        for i, key in enumerate(["usersnex"] + [k for k, *_r in updater.SOURCES]):
            cells = []
            for c, w in enumerate(widths):
                lab = ctk.CTkLabel(body, text="—", font=self.fonts.mono if c in (2, 3) else self.fonts.body,
                                   text_color=theme.TEXT if c == 0 else theme.TEXT_SOFT, width=w, anchor="w",
                                   height=22)
                lab.grid(row=3 + i, column=c, sticky="w")
                cells.append(lab)
            self.list_rows[key] = cells
        url_row = ctk.CTkFrame(body, fg_color="transparent")
        url_row.grid(row=9, column=0, columnspan=6, sticky="ew", pady=(10, 0))
        ctk.CTkLabel(url_row, text=tr("Indirizzo di usersnex.yaml").upper(), font=self.fonts.caption,
                     text_color=theme.TEXT_FAINT, anchor="w").pack(side="left", padx=(0, 8))
        self.ent_usx_url = ctk.CTkEntry(url_row, height=26, font=self.fonts.small, width=520,
                                        placeholder_text=tr("URL raw del repository di UserSnex (es. GitHub)"))
        self.ent_usx_url.insert(0, self.cfg.get("usersnex_list_url") or updater.DEFAULT_USERSNEX_URL)
        self.ent_usx_url.pack(side="left", fill="x", expand=True)
        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.grid(row=10, column=0, columnspan=6, sticky="w", pady=(10, 0))
        self.btn_update = button(btns, "⟳  " + tr("Aggiorna liste"), self._update_lists, primary=True, height=32)
        self.btn_update.pack(side="left")
        button(btns, tr("Apri cartella liste"),
               lambda: (os.makedirs(LISTS_DIR, exist_ok=True), self._open_path(LISTS_DIR))).pack(side="left",
                                                                                                padx=(8, 0))
        self.sw_autotest = ctk.CTkSwitch(btns, text=tr("Autotest dei siti nuovi dopo l'aggiornamento"),
                                         font=self.fonts.body)
        if self.cfg.get("autotest_after_update", False):
            self.sw_autotest.select()
        self.sw_autotest.pack(side="left", padx=(16, 0))

        mine = Card(page, tr("I miei siti (mytargets.yaml)"), self.fonts)
        mine.grid(row=2, column=0, sticky="nsew", pady=(theme.GAP, 0))
        mine.body.grid_columnconfigure(0, weight=1)
        mine.body.grid_rowconfigure(1, weight=1)
        mbtn = ctk.CTkFrame(mine.body, fg_color="transparent")
        mbtn.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        button(mbtn, "+  " + tr("Nuova risorsa"), self._new_target, primary=True).pack(side="left")
        button(mbtn, tr("Modifica"), self._edit_target).pack(side="left", padx=(8, 0))
        button(mbtn, tr("Elimina"), self._delete_target, danger=True).pack(side="left", padx=(8, 0))
        button(mbtn, tr("Apri file"), lambda: self._open_path(MYTARGETS_FILE)
               if os.path.isfile(MYTARGETS_FILE) else self.log(tr("mytargets.yaml non c'è ancora: aggiungi "
                                                                   "un sito con Nuova risorsa"), "warn")
               ).pack(side="left", padx=(8, 0))
        tags = {"high": theme.OK, "mid": theme.WARN, "low": theme.TEXT_SOFT, "err": theme.ERR,
                "dim": theme.TEXT_DIM}
        self.t_mine = Table(mine.body, [
            ("site", "Nome", 170, False, "w"), ("cat", "Categoria", 100, False, "w"),
            ("check", "Verifica", 200, False, "w"), ("state", "Stato", 130, False, "w"),
            ("url", "Profilo", 300, True, "w")], tags)
        self.t_mine.grid(row=1, column=0, sticky="nsew")
        self.t_mine.tree.bind("<Double-Button-1>", lambda _e: self._edit_target())
        self._fill_lists()
        self._fill_mine()

    def _avatars_changed(self) -> None:
        self.cfg["avatars"] = bool(self.sw_avatars.get())
        self._save_cfg()
        if self.cfg["avatars"] and self.results and not self.running and not self.avatar_thumbs:
            self._fetch_avatars()

    def _fill_mine(self) -> None:
        tree = self.t_mine.tree
        tree.delete(*tree.get_children())
        health = load_health()
        from ..gui.resource_form import MODE_LABELS
        for e in mytargets.load():
            name = str(e.get("name", ""))
            mode, text = mytargets.mode_of(e)
            check = tr(MODE_LABELS[mode]).split(":")[0] + (f": {text}" if text else "")
            h = health.get(name)
            state, tag = (tr(h["esito"]), "high" if h["esito"] == "ok" else "err") if h else (tr("da provare"), "low")
            tree.insert("", "end", iid="m|" + name, tags=(tag,),
                        values=(name, tr(e.get("category", "")), check, state, e.get("url", "")))
        self.t_mine.resort()
        self.tabbar.set_count("config", len(tree.get_children()) or "")

    def _selected_target(self) -> dict | None:
        sel = self.t_mine.tree.selection()
        if not sel:
            self.log(tr("seleziona un sito nella tabella I miei siti"), "warn")
            return None
        name = sel[0].split("|", 1)[1]
        return next((e for e in mytargets.load() if str(e.get("name")) == name), None)

    def _target_saved(self, name: str) -> None:
        self.log(tr("{name} salvato in mytargets.yaml", name=name), "ok")
        self._reload_sites()
        self._fill_mine()

    def _new_target(self) -> None:
        resource_form.open_form(self, self.fonts, sorted({s.category for s in self.sites}), self._target_saved)

    # ================================================================ siti: bando e modifica
    def _make_site_menu(self) -> tk.Menu:
        m = tk.Menu(self, tearoff=0, bg=theme.PANEL_2, fg=theme.TEXT, activebackground=theme.ACCENT_DIM,
                    activeforeground="#ffffff", bd=0, relief="flat", font=(theme.ui_font(), 10))
        return m

    def _site_popup(self, event, tree) -> None:
        iid = tree.identify_row(event.y)
        if not iid:
            return
        if iid not in tree.selection():
            tree.selection_set(iid)
        sites = self._sites_of(tree)
        m = self._site_menu
        m.delete(0, "end")
        if any(not s.banned for s in sites):
            m.add_command(label=tr("Metti al bando"), command=lambda: self._ban_sites(tree))
        if any(s.banned for s in sites):
            m.add_command(label=tr("Togli dal bando"), command=lambda: self._unban_sites(tree))
        if len(sites) == 1:
            m.add_command(label=tr("Modifica sito"), command=lambda: self._edit_site(tree))
        if m.index("end") is not None:
            m.tk_popup(event.x_root, event.y_root)

    def _sites_of(self, tree) -> list[SiteDef]:
        by_name = {s.name: s for s in self.sites}
        names = [iid.split("|", 1)[-1] for iid in tree.selection()]
        return [by_name[n] for n in names if n in by_name]

    def _ban_sites(self, tree) -> None:
        if self.running:
            self.log(tr("attendi la fine della ricerca"), "warn")
            return
        sites = [s for s in self._sites_of(tree) if not s.banned]
        if not sites:
            self.log(tr("seleziona uno o più siti da mettere al bando"), "warn")
            return
        banlist.ban(sites, reason="manuale")
        self.log(tr("al bando: {s}", s=", ".join(s.name for s in sites[:10]) + ("…" if len(sites) > 10 else "")),
                 "ok")
        self._reload_sites()

    def _unban_sites(self, tree) -> None:
        if self.running:
            self.log(tr("attendi la fine della ricerca"), "warn")
            return
        names = [s.name for s in self._sites_of(tree) if s.banned]
        if not names:
            self.log(tr("seleziona uno o più siti al bando"), "warn")
            return
        banlist.unban(names)
        self.log(tr("tornati attivi: {s}", s=", ".join(names[:10]) + ("…" if len(names) > 10 else "")), "ok")
        self._reload_sites()

    def _edit_site(self, tree) -> None:
        sites = self._sites_of(tree)
        if len(sites) != 1:
            self.log(tr("seleziona un sito da modificare"), "warn")
            return
        s = sites[0]
        mine = next((e for e in mytargets.load() if str(e.get("name", "")).lower() == s.name.lower()), None)
        if mine is not None:                       # è già uno dei miei siti: si modifica quello
            resource_form.open_form(self, self.fonts, sorted({x.category for x in self.sites}),
                                    self._target_saved, entry=mine)
        else:
            resource_form.open_form(self, self.fonts, sorted({x.category for x in self.sites}),
                                    self._target_saved, entry=to_entry(s), from_list=True)

    def _edit_target(self) -> None:
        e = self._selected_target()
        if e is not None:
            resource_form.open_form(self, self.fonts, sorted({s.category for s in self.sites}),
                                    self._target_saved, entry=e)

    def _delete_target(self) -> None:
        e = self._selected_target()
        if e is None:
            return
        name = str(e.get("name"))

        def ok() -> None:
            mytargets.delete(name)
            self.log(tr("{name} tolto da mytargets.yaml", name=name), "ok")
            self._reload_sites()
            self._fill_mine()

        confirm(self, tr("Elimina"), tr("Togliere {name} dai tuoi siti?", name=name), tr("Elimina"), ok,
                self.fonts, cancel_text=tr("Annulla"))

    def _reload_sites(self) -> None:
        if self.running:
            return
        self._load_sites()
        self._fill_sites()
        self._status("SITI", tr("{a} attivi su {n}", a=self.active_sites, n=len(self.sites)), theme.OK)

    def _fill_lists(self, results: dict | None = None) -> None:
        results = results or {}
        url = self.cfg.get("usersnex_list_url") or updater.DEFAULT_USERSNEX_URL
        for key, d in updater.info(url).items():
            cells = self.list_rows[key]
            n = d.get("sites")
            r = results.get(key, {})
            esito, color = "", theme.TEXT_SOFT
            if r.get("esito") == "aggiornata":
                prima = r.get("prima")
                esito = tr("aggiornata") + ("" if prima is None else f"  ({r['sites'] - prima:+d})")
                color = theme.OK
            elif r.get("esito") == "invariata":
                esito, color = tr("già aggiornata"), theme.OK
            elif r.get("esito") == "errore":
                esito, color = tr("errore: {e}", e=r.get("errore", ""))[:60], theme.ERR
            elif d.get("missing"):
                esito, color = tr("da scaricare"), theme.WARN
            elif key == "usersnex" and not url:
                esito = tr("inclusa (indirizzo non impostato)")
            elif d.get("bundled"):
                esito = tr("lista inclusa")
            cells[0].configure(text=d["name"])
            cells[1].configure(text=d["license"])
            cells[2].configure(text="—" if n is None else str(n))
            cells[3].configure(text=d.get("updated", "—"))
            cells[4].configure(text=esito, text_color=color)

    def _update_lists(self) -> None:
        if self.running:
            self.log(tr("attendi la fine della ricerca"), "warn")
            return
        self.running = True
        self._t0 = time.monotonic()
        self.cancel.clear()
        self.cfg["autotest_after_update"] = bool(self.sw_autotest.get())
        self.cfg["usersnex_list_url"] = self.ent_usx_url.get().strip() or updater.DEFAULT_USERSNEX_URL
        self._save_cfg()
        self.btn_update.configure(state="disabled")
        self.btn_search.configure(state="disabled")
        self.btn_selftest.configure(state="disabled")
        self._show_state(tr("Aggiornamento liste"), theme.WARN)
        self.progress.set(0)
        self.progress.grid()
        self.log(tr("aggiornamento delle liste di siti"))
        before = {s.name for s in self.sites}
        steps = {"k": 0}

        def log(msg: str, kind: str = "info") -> None:
            steps["k"] += 1
            self.ui_queue.put(("progress", min(steps["k"] / 9, 1.0)))
            self.log(msg, "" if kind == "info" else kind)

        def work() -> None:
            try:
                res = updater.update(log=log, cancel=self.cancel, usersnex_url=self.cfg["usersnex_list_url"])
            except Exception as e:  # noqa: BLE001
                self.log(tr("aggiornamento non riuscito: {e}", e=e), "err")
                res = {}
            self.ui_queue.put(("lists", (res, before)))

        threading.Thread(target=work, daemon=True).start()

    def _on_lists(self, res: dict, before: set[str]) -> None:
        self.running = False
        self.btn_update.configure(state="normal")
        self.btn_search.configure(state="normal")
        self.btn_selftest.configure(state="normal")
        self.progress.grid_remove()
        self._fill_lists(res)
        self._status("TEMPO", f"{time.monotonic() - self._t0:.1f} s", theme.OK)
        if any(r.get("esito") == "aggiornata" for r in res.values()):
            self._load_sites()
            self._fill_sites()
            self._status("SITI", tr("{a} attivi su {n}", a=self.active_sites, n=len(self.sites)), theme.OK)
            new = [s for s in self.sites if s.name not in before]
            gone = len(before - {s.name for s in self.sites})
            self.log(tr("liste ricaricate: {a} siti nuovi, {b} tolti", a=len(new), b=gone), "ok")
            self.log(self.site_breakdown())
            if new and self.cfg.get("autotest_after_update"):
                self._show_state(tr("Pronto"), theme.OK)
                self._selftest(only=new)
                return
        self._show_state(tr("Pronto"), theme.OK)

    def _fill_sites(self) -> None:
        tree = self.t_sites.tree
        tree.delete(*tree.get_children())
        health = load_health()
        q = self.filter.get().strip().lower() if hasattr(self, "filter") else ""
        for s in self.sites:
            if q and q not in f"{s.name} {s.category} {tr(s.category)} {s.check_url} {s.source}".lower():
                continue
            h = health.get(s.name, {})
            if s.banned:
                state, tag = tr("al bando"), "err"
            elif s.extended and s.enabled and not self.cfg.get("extended"):
                state, tag = tr("lista estesa"), "dim"
            elif s.protection and not s.enabled:
                state, tag = tr("off · {p}", p=", ".join(s.protection)), "dim"
            elif h and h["esito"] != "ok":
                state, tag = tr("off · {e}", e=tr(h["esito"])), "err"
            elif h:
                state, tag = tr("ok (autotest)"), "high"
            else:
                state, tag = tr("attivo"), "low"
            tree.insert("", "end", iid="s|" + s.name, tags=(tag,),
                        values=(s.name, tr(s.category), state, s.source.split(" (")[0], s.check_url))
        self.t_sites.resort()
        self.tabbar.set_count("sites", len(self.sites))

    def _build_details(self, parent) -> None:
        right = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, width=DETAILS_WIDTH)
        right.grid(row=0, column=1, rowspan=3, sticky="ns", padx=(theme.GAP, 0))
        right.grid_propagate(False)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=3)
        right.grid_rowconfigure(1, weight=2)

        det = Card(right, tr("Dettagli"), self.fonts)
        det.grid(row=0, column=0, sticky="nsew")
        self.btn_open = ctk.CTkButton(det.head, text=tr("Apri profilo"), width=0, height=18, corner_radius=3,
                                      fg_color="transparent", border_width=1, border_color=theme.BORDER_STRONG,
                                      hover_color=theme.HOVER, text_color=theme.ACCENT, font=self.fonts.small,
                                      command=lambda: self._open_site(self.selected))
        self.btn_open.pack(side="right")
        self.det_avatar = ctk.CTkLabel(det.body, text="", anchor="w")
        self.details = ctk.CTkTextbox(det.body, font=self.fonts.mono, wrap="word")
        self.details.pack(fill="both", expand=True)
        self._set_details(tr(HELP))

        log = Card(right, "Log", self.fonts)
        log.grid(row=1, column=0, sticky="nsew", pady=(theme.GAP, 0))
        self.console = ctk.CTkTextbox(log.body, font=ctk.CTkFont(family=theme.mono_font(), size=10),
                                      text_color=theme.TEXT_SOFT, wrap="word")
        self.console.pack(fill="both", expand=True)
        self.console.tag_config("ok", foreground=theme.OK)
        self.console.tag_config("err", foreground=theme.ERR)
        self.console.tag_config("warn", foreground=theme.WARN)
        self.console.configure(state="disabled")

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0, height=26)
        footer.grid(row=4, column=0, sticky="ew")
        footer.grid_propagate(False)
        footer.grid_columnconfigure(0, weight=1)
        footer.grid_rowconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(footer, text=tr("Pronto."), font=self.fonts.small,
                                         text_color=theme.TEXT_SOFT, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="ew", padx=(16, 8))
        self.progress = ctk.CTkProgressBar(footer, width=120, height=4)
        self.progress.grid(row=0, column=1, padx=(0, 14))
        self.progress.set(0)
        self.progress.grid_remove()
        ctk.CTkLabel(footer, text=tr("Invio  avvia ricerca"), font=self.fonts.tiny,
                     text_color=theme.TEXT_FAINT).grid(row=0, column=3, sticky="e", padx=(0, 16))
        credits = ctk.CTkLabel(footer, text=CREDITS + "  ·  " + tr("Info e licenze"), font=self.fonts.tiny,
                               text_color=theme.TEXT_DIM, cursor="hand2")
        credits.grid(row=0, column=4, sticky="e", padx=(0, 16))
        credits.bind("<Button-1>", lambda _e: about.show(self, self.fonts))

    # ================================================================ helper
    def log(self, msg: str, kind: str = "") -> None:
        """Thread-safe: i messaggi passano dalla coda letta dal thread dell'interfaccia."""
        self.ui_queue.put(("log", time.strftime("%H:%M:%S") + "  " + str(msg), kind))

    def _write_log(self, line: str, kind: str) -> None:
        self.console.configure(state="normal")
        self.console.insert("end", line + "\n", kind or None)
        self.console.see("end")
        self.console.configure(state="disabled")
        self.status_label.configure(text=line.split("  ", 1)[-1],
                                    text_color=theme.ERR if kind == "err" else theme.TEXT_SOFT)

    def _set_details(self, text: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def _hint(self, text: str) -> None:
        if text:
            self.hint_label.configure(text=text)
            self.hint_label.grid()
        else:
            self.hint_label.grid_remove()

    def _status(self, key: str, text: str, color: str = theme.TEXT_FAINT) -> None:
        dot, val = self.status_rows[key]
        dot.configure(text_color=color)
        val.configure(text=text)

    def _show_state(self, text: str, color: str) -> None:
        self.state_dot.configure(text_color=color)
        self.state_label.configure(text=text, text_color=theme.TEXT if color == theme.OK else theme.TEXT_SOFT)

    def _opt_changed(self) -> None:
        self.cfg["use_cache"] = bool(self.sw_cache.get())
        self.cfg["extended"] = bool(self.sw_ext.get())
        self._status("SITI", tr("{a} attivi su {n}", a=self.active_sites, n=len(self.sites)), theme.OK)
        self.tabbar.set_count("sites", len(self.sites))
        self._save_cfg()
        self._refill_tables()
        self.log(self.site_breakdown())

    def _set_language(self, name: str) -> None:
        code = next((c for c, n in LANGUAGES.items() if n == name), "it")
        if code == language():
            return
        if self.running:
            self.lang_menu.set(LANGUAGES[language()])
            self.log(tr("attendi la fine della ricerca"), "warn")
            return
        set_language(code)
        self.cfg["language"] = code
        self._save_cfg()
        self._rebuild()

    def _rebuild(self) -> None:
        """Ricostruisce l'interfaccia nella nuova lingua senza perdere risultati né ricerca."""
        tab, user = self.tab, self.user_entry.get()
        seed = {k: e.get() for k, e in self.seed_entries.items()}
        log = self.console.get("1.0", "end").rstrip("\n")
        self.graph.active = False
        for w in self.winfo_children():
            w.destroy()
        self.tab = ""
        self._build()
        if user:
            self.user_entry.insert(0, user)
        for k, v in seed.items():
            if v:
                self.seed_entries[k].insert(0, v)
        if log:
            self.console.configure(state="normal")
            self.console.insert("end", log + "\n")
            self.console.configure(state="disabled")
            self.console.see("end")
        self._avatar_cache.clear()               # le immagini Tk appartengono ai widget appena distrutti
        if self.results:
            self.graph.set_avatars(self.avatar_thumbs)
            self._refill_tables()
            self.graph.set_report(self.report, self._found_names())
        self._update_stats()
        self._show_tab(tab or "results")

    def _open_manual(self) -> None:
        """Il manuale docs/manual/<lingua>/MANUAL.md in una finestra del programma (inglese se la lingua manca)."""
        path = manual_path(language())
        if not path:
            self.log(tr("manuale non trovato: {p}", p=os.path.join(MANUAL_DIR, language(), "MANUAL.md")), "err")
            return
        try:
            manual.open_manual(self, path)
        except Exception as e:  # noqa: BLE001
            self.log(tr("impossibile aprire il manuale: {e}", e=e), "err")

    def _clear_cache(self) -> None:
        n = DiskCache(Path(CACHE_DIR)).clear()
        self.log(tr("cache svuotata: {n} risposte", n=n))

    def _open_path(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)
        about._open(path)

    def _open_site(self, site: str | None) -> None:
        r = self.results.get(site or "")
        if r:
            webbrowser.open(r.url)

    def _tk_exception(self, exc, val, tb) -> None:
        self._write_log(f"errore interno: {val}", "err")
        traceback.print_exception(exc, val, tb)

    # ================================================================ schede
    def _show_tab(self, key: str) -> None:
        if self.tab in self.pages:
            self.pages[self.tab].grid_remove()
        self.tab = key
        self.pages[key].grid(row=2, column=0, sticky="nsew", pady=(theme.GAP, 0))
        self.tabbar.select(key)
        if key == "graph":
            self.graph.select(self.selected)

    def _tree_site(self, tree) -> str | None:
        sel = tree.selection()
        if not sel:
            return None
        return sel[0].split("|", 1)[-1]

    def _on_tree_select(self, tree) -> None:
        site = self._tree_site(tree)
        if site:
            self._select(site)

    # ================================================================ ricerca
    def _toggle_search(self) -> None:
        if self.running:
            self.cancel.set()
            self.log(tr("interruzione richiesta…"), "warn")
            return
        username = self.user_entry.get().strip().lstrip("@")
        if not username:
            self._hint(tr("Scrivi un username nel campo in alto e premi Invio."))
            self.user_entry.focus_set()
            return
        self._hint("")
        self.searched = username
        self.results.clear()
        self.avatar_thumbs = {}
        self.graph.set_avatars({})
        self.report = None
        self.selected = None
        self.cancel.clear()
        self.running = True
        self._t0 = time.monotonic()
        for t in (self.t_results, self.t_identity, self.t_missing, self.t_errors):
            t.tree.delete(*t.tree.get_children())
        self.graph.set_report(None)
        self._set_details(tr(HELP))
        self.page_title.configure(text=tr("Ricerca di {u}", u=username))
        self.btn_search.configure(text="■  " + tr("Interrompi"), fg_color="transparent", border_width=1,
                                  border_color=theme.ERR, text_color=theme.ERR, hover_color=theme.HOVER)
        self._show_state(tr("Ricerca in corso"), theme.WARN)
        self._status("RICERCA", username, theme.WARN)
        self.progress.set(0)
        self.progress.grid()
        self._update_stats()
        self.log(tr("cerco {u} su {n} siti", u=username, n=self.active_sites))
        self.log(self.site_breakdown())
        self._n_found_rows = 0
        self._live_found = 0
        self._live_t = 0.0
        cache = DiskCache(Path(CACHE_DIR)) if self.sw_cache.get() else None
        engine = Engine(self.sites, cache=cache, extended=bool(self.cfg.get("extended")))

        def work() -> None:
            try:
                results = engine.run(username, on_result=lambda r: self.ui_queue.put(("result", r)),
                                     cancel=self.cancel)
                self.ui_queue.put(("done", results))
            except Exception as e:  # noqa: BLE001
                self.ui_queue.put(("log", time.strftime("%H:%M:%S") + f"  errore: {e}", "err"))
                self.ui_queue.put(("done", list(self.results.values())))

        threading.Thread(target=work, daemon=True).start()

    def _tick(self) -> None:
        try:
            for _ in range(200):
                item = self.ui_queue.get_nowait()
                if item[0] == "log":
                    self._write_log(item[1], item[2])
                elif item[0] == "result":
                    self._on_result(item[1])
                elif item[0] == "done":
                    self._on_done()
                elif item[0] == "progress":
                    self.progress.set(item[1])
                elif item[0] == "selftest":
                    self._on_selftest(item[1])
                elif item[0] == "lists":
                    self._on_lists(*item[1])
                elif item[0] == "avatars":
                    self._on_avatars(*item[1])
        except queue.Empty:
            pass
        if self.running:
            self._status("TEMPO", f"{time.monotonic() - self._t0:.1f} s", theme.WARN)
            self._live_correlate()
        self.after(100, self._tick)

    def _on_result(self, r: SiteResult) -> None:
        if r.username != self.searched:
            return
        self.results[r.site] = r
        if r.status == Status.FOUND:
            self.log(f"[+] {r.site}  {r.url}", "ok")
        self._insert_row(r)
        self.progress.set(len(self.results) / max(self.active_sites, 1))
        self._update_stats()

    def _on_done(self) -> None:
        self.running = False
        stopped = self.cancel.is_set()
        self.btn_search.configure(text="▶  " + tr("Avvia ricerca"), fg_color=theme.BTN, border_width=0,
                                  text_color=theme.BTN_TEXT, hover_color=theme.BTN_HOVER)
        self.progress.grid_remove()
        elapsed = time.monotonic() - self._t0
        self._status("TEMPO", f"{elapsed:.1f} s", theme.OK)
        self._status("RICERCA", self.searched, theme.OK if not stopped else theme.WARN)
        self._show_state(tr("Interrotta") if stopped else tr("Completata"), theme.WARN if stopped else theme.OK)
        self._recorrelate(force=True)
        found = sum(r.status == Status.FOUND for r in self.results.values())
        errors = sum(r.status == Status.ERROR for r in self.results.values())
        missing = len(self.results) - found - errors
        self.log(tr("fine: {f} account trovati su {n} siti in {s:.1f} s", f=found, n=len(self.results), s=elapsed),
                 "ok")
        rest = self.active_sites - len(self.results)
        self.log(tr("conti: {n} controllati = {f} trovati + {m} non trovati + {e} errori", n=len(self.results),
                    f=found, m=missing, e=errors)
                 + (tr("  ·  {r} non controllati (interrotta)", r=rest) if rest > 0 else ""))
        if self.report and self.report.clusters:
            self.log(tr("identità principale: {n} account", n=len(self.report.clusters[0])), "ok")
            self._show_tab("identity")
        elif found > 1:
            self.log(tr("nessun legame tra gli account: probabilmente persone diverse con lo stesso username"), "warn")
        if self.cfg.get("avatars", True) and found:
            self._fetch_avatars()

    # ================================================================ avatar
    def _fetch_avatars(self) -> None:
        found = [r for r in self.results.values() if r.status == Status.FOUND and r.profile.avatar]
        if not found:
            return
        who = self.searched
        self.log(tr("scarico {n} avatar per confrontare le immagini…", n=len(found)))

        def work() -> None:
            try:
                got = avatars.fetch(found, cancel=self.cancel)
            except Exception as e:  # noqa: BLE001
                self.log(tr("avatar non scaricati: {e}", e=e), "err")
                got = {}
            self.ui_queue.put(("avatars", (who, got)))

        threading.Thread(target=work, daemon=True).start()

    def _on_avatars(self, who: str, got: dict) -> None:
        if who != self.searched or self.running:
            return
        self.avatar_thumbs = {site: path for site, (fp, path) in got.items() if fp != "-"}
        self._avatar_cache.clear()
        blank = sum(1 for fp, _p in got.values() if fp == "-")
        self.log(tr("avatar: {n} scaricati", n=len(got)) + (tr(", {b} vuoti o predefiniti", b=blank) if blank else ""),
                 "ok")
        before = {a.site: a.confidence for a in self.report.accounts} if self.report else {}
        self.graph.set_avatars(self.avatar_thumbs)
        self._recorrelate(force=True)
        if self.report:
            changed = sum(1 for a in self.report.accounts if a.confidence != before.get(a.site, a.confidence))
            if changed:
                self.log(tr("punteggi aggiornati con il confronto degli avatar: {n} account", n=changed), "ok")

    def _avatar_pil(self, site: str, size: int):
        """Miniatura dell'avatar come immagine PIL rotonda (RGBA), None se non c'è."""
        path = self.avatar_thumbs.get(site)
        if not path:
            return None
        try:
            from PIL import Image, ImageDraw
            big = size * 3
            with Image.open(path) as src:
                face = src.convert("RGB").resize((big, big), Image.Resampling.LANCZOS)
            mask = Image.new("L", (big, big), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
            face.putalpha(mask)
            return face.resize((size, size), Image.Resampling.LANCZOS)
        except Exception:  # noqa: BLE001
            return None

    def _avatar_image(self, site: str, size: int):
        """Miniatura dell'avatar come immagine Tk (per le righe delle tabelle), None se non c'è."""
        key = (site, size)
        img = self._avatar_cache.get(key)
        if img is None:
            pil = self._avatar_pil(site, size)
            if pil is None:
                return None
            img = theme.photo(pil, self)
            self._avatar_cache[key] = img
        return img

    def _row_avatar(self, site: str):
        img = self._avatar_image(site, int(20 * theme.scaling(self)))
        return img if img is not None else ""

    # ================================================================ autotest
    def _selftest(self, only: list[SiteDef] | None = None) -> None:
        if self.running:
            self.log(tr("attendi la fine della ricerca"), "warn")
            return
        targets = only if only is not None else self.sites
        for s in targets:                       # si riprovano anche i siti bocciati l'ultima volta
            if not s.protection:
                s.enabled = True
        engine = Engine(self.sites, extended=bool(self.cfg.get("extended")))
        n = sum(1 for s in targets if engine.active(s) and s.known)
        self.running = True
        self.cancel.clear()
        self.btn_selftest.configure(state="disabled")
        self._show_state(tr("Autotest siti"), theme.WARN)
        self.progress.set(0)
        self.progress.grid()
        self.log(tr("autotest di {n} siti: un account vero e uno inventato per ciascuno", n=n))
        done = {"k": 0}

        def tick(_r) -> None:
            done["k"] += 1
            self.ui_queue.put(("progress", done["k"] / max(2 * n, 1)))

        def work() -> None:
            try:
                report = run_selftest(targets, engine, on_progress=tick, cancel=self.cancel)
                if only is not None:            # solo i siti nuovi: si aggiungono ai risultati precedenti
                    merged = load_health()
                    merged.update(report)
                    save_health(merged)
                else:
                    save_health(report)
                self.ui_queue.put(("selftest", report))
            except Exception as e:  # noqa: BLE001
                self.ui_queue.put(("log", time.strftime("%H:%M:%S") + f"  autotest: {e}", "err"))
                self.ui_queue.put(("selftest", {}))

        threading.Thread(target=work, daemon=True).start()

    def _on_selftest(self, report: dict) -> None:
        self.running = False
        self.btn_selftest.configure(state="normal")
        self.progress.grid_remove()
        self.skipped = apply_health(self.sites)
        self._fill_sites()
        self._fill_mine()
        self._status("SITI", tr("{a} attivi su {n}", a=self.active_sites, n=len(self.sites)), theme.OK)
        counts: dict[str, int] = {}
        for h in report.values():
            counts[h["esito"]] = counts.get(h["esito"], 0) + 1
        self.log(tr("autotest: ") + "  ·  ".join(f"{k} {v}" for k, v in sorted(counts.items())), "ok")
        self.log(self.site_breakdown())
        self._show_state(tr("Pronto"), theme.OK)
        self._show_tab("sites")

    # ================================================================ correlazione
    def _seed(self) -> Profile:
        e = self.seed_entries
        link = e["link"].get().strip()
        return Profile(display_name=e["name"].get().strip(), location=e["location"].get().strip(),
                       links=[link] if link else [])

    def _recorrelate(self, force: bool = False) -> None:
        if self.running or not self.results:
            return
        seed = self._seed()
        key = (seed.display_name, seed.location, tuple(seed.links))
        if not force and key == getattr(self, "_seed_key", None):
            return
        self._seed_key = key
        self.report = correlate(list(self.results.values()), seed)
        self._refill_tables()
        self.graph.set_report(self.report, self._found_names())
        self._update_stats()
        if self.selected:
            self._select(self.selected)
        if not force:
            self.log(tr("punteggi ricalcolati con gli indizi noti"))

    def _live_correlate(self) -> None:
        """Durante la ricerca: ogni 2 s, se ci sono trovati nuovi, aggiorna punteggi, identità e grafo."""
        if not self.searched or time.monotonic() - getattr(self, "_live_t", 0.0) < 2.0:
            return
        found = [r for r in self.results.values() if r.status == Status.FOUND]
        if len(found) == getattr(self, "_live_found", 0):
            return
        self._live_found, self._live_t = len(found), time.monotonic()
        self.report = correlate(found, self._seed())
        tree = self.t_results.tree
        for a in self.report.accounts:
            iid = "r|" + a.site
            if tree.exists(iid):
                vals = list(tree.item(iid, "values"))
                vals[0] = f"{a.confidence}%"
                vals[6] = f"#{a.cluster + 1}" if a.cluster >= 0 else "—"
                tree.item(iid, values=vals, tags=(_conf_tag(a.confidence),))
        ti = self.t_identity.tree
        ti.delete(*ti.get_children())
        for a in self.report.accounts:
            r = self.results.get(a.site)
            if not r or not self._matches(r):
                continue
            signals = ", ".join(dict.fromkeys(tr(e.signal) for e in a.evidence[1:])) or tr("solo username uguale")
            ti.insert("", "end", iid="i|" + a.site, tags=(_conf_tag(a.confidence),),
                      values=(f"#{a.cluster + 1}" if a.cluster >= 0 else "—", a.site, f"{a.confidence}%", signals))
        self.graph.set_report(self.report, self._found_names())
        self._update_stats()

    # ================================================================ tabelle
    def _matches(self, r: SiteResult) -> bool:
        q = self.filter.get().strip().lower()
        if not q:
            return True
        p = r.profile
        hay = " ".join([r.site, r.category, r.url, p.display_name, p.location, p.bio, " ".join(p.links)]).lower()
        return q in hay

    def _insert_row(self, r: SiteResult) -> None:
        if not self._matches(r):
            return
        acc = self.report.account(r.site) if self.report else None
        if r.status == Status.FOUND:
            conf = f"{acc.confidence}%" if acc else "…"
            grp = f"#{acc.cluster + 1}" if acc and acc.cluster >= 0 else "—" if acc else ""
            tag = _conf_tag(acc.confidence) if acc else "low"
            pos = getattr(self, "_n_found_rows", 0)
            self._n_found_rows = pos + 1            # i trovati restano in cima, anche durante la ricerca
            self.t_results.tree.insert("", pos, iid="r|" + r.site, tags=(tag,), image=self._row_avatar(r.site),
                                       values=(conf, r.site, tr(r.category), r.profile.display_name,
                                               r.profile.location, r.url, grp, r.elapsed_ms))
            self.t_results.resort_later()
        elif r.status == Status.ERROR:
            self.t_errors.tree.insert("", "end", iid="e|" + r.site, tags=("err",),
                                      values=(r.site, r.http_status or "—", r.error, r.url))
            self.t_errors.resort_later()
        else:
            self.t_missing.tree.insert("", "end", iid="m|" + r.site, tags=("dim",),
                                       values=(r.site, tr(r.category), r.url, r.error or tr("non trovato")))
            self.t_missing.resort_later()

    def _found_names(self) -> dict[str, str]:
        """Nome visualizzato trovato su ciascun profilo: diventano i nodi dei nomi nel grafo."""
        return {r.site: r.profile.display_name for r in self.results.values()
                if r.status == Status.FOUND and r.profile.display_name}

    def _refill_tables(self) -> None:
        for t in (self.t_results, self.t_identity, self.t_missing, self.t_errors):
            t.tree.delete(*t.tree.get_children())
        self._n_found_rows = 0
        order = {a.site: i for i, a in enumerate(self.report.accounts)} if self.report else {}
        rows = sorted(self.results.values(), key=lambda r: (r.status != Status.FOUND, order.get(r.site, 999),
                                                            r.site.lower()))
        for r in rows:
            self._insert_row(r)
        if self.report:
            for a in self.report.accounts:
                r = self.results.get(a.site)
                if not r or not self._matches(r):
                    continue
                signals = ", ".join(dict.fromkeys(tr(e.signal) for e in a.evidence[1:])) or tr("solo username uguale")
                grp = f"#{a.cluster + 1}" if a.cluster >= 0 else "—"
                self.t_identity.tree.insert("", "end", iid="i|" + a.site, tags=(_conf_tag(a.confidence),),
                                            values=(grp, a.site, f"{a.confidence}%", signals))
        for t in (self.t_results, self.t_identity, self.t_missing, self.t_errors):
            t.resort()

    def _update_stats(self) -> None:
        res = list(self.results.values())
        found = sum(r.status == Status.FOUND for r in res)
        errors = sum(r.status == Status.ERROR for r in res)
        missing = len(res) - found - errors
        self.c_checked.set(f"{len(res)}/{self.active_sites}")
        self.c_found.set(found)
        self.c_errors.set(errors)
        if self.report:
            self.c_identity.set(len(self.report.clusters[0]) if self.report.clusters else 0)
            self.c_unique.set(f"{int(self.report.username_uniqueness * 100)}%")
        else:
            self.c_identity.set("…" if self.running else 0)
            self.c_unique.set("—")
        self.tabbar.set_count("results", found, theme.OK if found else None)
        self.tabbar.set_count("identity", len(self.report.clusters[0]) if self.report and self.report.clusters else "")
        self.tabbar.set_count("graph", found or "")
        self.tabbar.set_count("missing", missing or "")
        self.tabbar.set_count("errors", errors or "", theme.ERR if errors else None)

    # ================================================================ dettagli
    def _show_det_avatar(self, site: str | None) -> None:
        pil = self._avatar_pil(site, 128) if site else None
        if pil is None:
            self.det_avatar.pack_forget()
            return
        try:
            img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(64, 64))     # nitida anche in HiDPI
        except ImportError:                                                    # Pillow senza ImageTk
            img = theme.photo(pil.resize((64, 64)), self)
        self.det_avatar.configure(image=img)
        self.det_avatar.image = img
        if not self.det_avatar.winfo_ismapped():
            self.det_avatar.pack(side="top", anchor="w", pady=(0, 6), before=self.details)

    def _select(self, site: str) -> None:
        self.selected = site
        self._show_det_avatar(site)
        if self.tab == "graph":
            self.graph.select(site)
        r = self.results.get(site)
        if r is None:
            sd = next((s for s in self.sites if s.name == site), None)
            if sd:
                h = load_health().get(sd.name, {})
                self._set_details("\n".join([
                    sd.name, tr(sd.category), "", _kv("interroga", sd.check_url), _kv("profilo", sd.url),
                    _kv("stato", tr("attivo") if sd.enabled else tr("disattivato")),
                    _kv("autotest", f"{tr(h['esito'])}  {h.get('nota', '')}" if h else "—"),
                    _kv("protezione", ", ".join(sd.protection) or "—"), _kv("noti", ", ".join(sd.known) or "—"),
                    _kv("req/s", f"{sd.rate_per_sec:g}"), _kv("regex", sd.regex or "—"), "", _kv("fonte", sd.source)]))
            return
        p = r.profile
        lines = [r.site, r.url, ""]
        if r.status != Status.FOUND:
            lines += [_kv("stato", tr("errore") if r.status == Status.ERROR else tr("non trovato")),
                      _kv("http", r.http_status or "—"), _kv("nota", r.error or "—")]
            self._set_details("\n".join(lines))
            return
        acc = self.report.account(site) if self.report else None
        if acc:
            lines.append(_kv("confidenza", f"{acc.confidence}%"))
            lines.append(_kv("gruppo", f"#{acc.cluster + 1}" if acc.cluster >= 0 else tr("nessuno")))
        lines += [_kv("nome", p.display_name or "—"), _kv("località", p.location or "—"),
                  _kv("creato", p.created_at or "—"), _kv("http", f"{r.http_status}  ·  {r.elapsed_ms} ms")]
        if p.bio:
            lines += ["", "BIO", p.bio[:600]]
        if p.links:
            lines += ["", "LINK"] + [f" · {x}" for x in p.links[:12]]
        if p.avatar:
            lines += ["", "AVATAR", p.avatar]
        if acc:
            lines += ["", tr("Indizi").upper()]
            for e in acc.evidence:
                lines.append(f" + {tr(e.signal)}  ({int(e.weight * 100)}%)")
                if e.detail:
                    lines.append(f"   {e.detail[:120]}")
        self._set_details("\n".join(lines))

    # ================================================================ esportazione
    def _ready_for_export(self) -> bool:
        if not self.results or self.running:
            self.log(tr("niente da esportare: fai prima una ricerca") if not self.results
                     else tr("attendi la fine della ricerca"), "warn")
            return False
        if self.report is None:
            self._recorrelate(force=True)
        return True

    def _export(self) -> None:
        if self._ready_for_export():
            files = export_json_csv(list(self.results.values()), self.report, RESULTS_DIR)
            self.log(tr("salvati: {f}", f=", ".join(os.path.basename(f) for f in files)), "ok")

    def _report(self) -> None:
        if self._ready_for_export():
            path = export_html(list(self.results.values()), self.report, RESULTS_DIR)
            self.log(tr("report: {f}", f=os.path.basename(path)), "ok")
            webbrowser.open("file://" + os.path.abspath(path))

    def _on_close(self) -> None:
        self.cancel.set()
        self.destroy()


def _kv(label: str, value) -> str:
    label = tr(label)
    return f"{label:<11}{value}" if len(label) < 11 else f"{label} {value}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="UserSnex GUI")
    ap.add_argument("username", nargs="?", default="", help="username da cercare subito")
    a = ap.parse_args(argv)
    App(a.username).mainloop()
    return 0
