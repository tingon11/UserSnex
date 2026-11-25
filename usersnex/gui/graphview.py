# UserSnex — grafo delle identità.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Grafo delle identità, nello stesso stile del grafo di DroidSnex / OSInt Graph: nodi tondi
chiari con bordo colorato e icona, etichetta sotto, collegamenti curvi, disposizione "organica"
a forze.

    username ── gruppo (identità principale, altri gruppi, senza legami) ── account
                                 account ┄┄ indizio ┄┄ account

Tutto su un Canvas Tk: trascina i nodi, rotella = zoom, trascina lo sfondo = sposta,
clic = evidenzia il nodo e i suoi vicini (gli altri si attenuano), doppio clic su un account =
apre il profilo, doppio clic su un gruppo = richiude/riapre il ramo. La disposizione gira in
un thread a parte (Fruchterman-Reingold con raffreddamento) e il disegno segue a 20 fps
finché i nodi si muovono.

Riquadro LAYOUT: Organico (predefinito), Circolare, Gerarchico, Griglia e "Riduci incroci"
(più disposizioni organiche con semi diversi, si tiene quella con meno archi incrociati).
"""
from __future__ import annotations

import math
import random
import threading
import time
import unicodedata
import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable

import customtkinter as ctk

from ..correlate import LINK_THRESHOLD, SEED, IdentityReport
from ..i18n import tr
from . import theme

IDEAL = 110.0          # distanza ideale fra nodi collegati
REPULSION = 9000.0
GRAVITY = 0.015
EDGE_COLOR = "#94a3b8"
NODE_BG = "#ffffff"
TARGET_BG = "#fef3c7"
TARGET_BORDER = "#b45309"
SEED_COLOR = "#0891b2"
LOOSE_COLOR = "#64748b"
STRONG_COLOR = "#059669"
WEAK_COLOR = "#d97706"
CLUSTER_COLORS = ["#059669", "#7c3aed", "#db2777", "#2563eb", "#ea580c", "#65a30d", "#0d9488", "#9333ea"]
NAME_COLOR = "#0ea5e9"
KIND_SIZE = {"user": 58, "group": 42, "seed": 36, "account": 30, "name": 24}
RADIUS = {"group": IDEAL * 1.6, "seed": IDEAL * 1.4, "account": IDEAL * 2.4, "name": IDEAL * 3.0}
LEVEL = {"user": 0, "seed": 1, "group": 1, "account": 2, "name": 3}
LAYOUTS = ("organic", "circle", "tree", "grid")
TREE_WRAP = 8               # Gerarchico: oltre 8 foglie sotto un nodo vanno a blocchi di righe
UNTANGLE_RUNS = 12
UNTANGLE_BUDGET = 4.0
COLLAPSE_OVER = 30          # "Senza legami" con più di 30 account parte chiuso
MIN_EDGE = 0.25             # indizi più deboli non vengono disegnati (troppi archi inutili)
MAX_EVIDENCE_EDGES = 400


def _mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def cluster_color(idx: int) -> str:
    return CLUSTER_COLORS[idx % len(CLUSTER_COLORS)] if idx >= 0 else LOOSE_COLOR


def _initials(name: str) -> str:
    words = [w for w in name.replace(".", " ").replace("-", " ").replace("_", " ").split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    w = words[0] if words else "?"
    caps = [c for c in w[1:] if c.isupper()]
    return (w[0] + (caps[0] if caps else w[1:2])).upper() if len(w) > 1 else w.upper()


@dataclass
class Node:
    id: str
    kind: str
    label: str
    color: str = ""
    glyph: str = ""
    size: int = 30
    parent: str | None = None
    info: dict = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    fixed: bool = False
    placed: bool = False


@dataclass
class Edge:
    a: str
    b: str
    weight: float = 1.0
    color: str = EDGE_COLOR
    dashed: bool = False
    tree: bool = True            # arco della struttura (username → gruppo → account) o indizio
    label: str = ""


def norm_name(name: str) -> str:
    """Forma confrontabile di un nome visualizzato: minuscole, senza accenti, emoji e punteggiatura."""
    t = unicodedata.normalize("NFKD", (name or "").casefold())
    t = "".join(c if c.isalnum() else " " for c in t if not unicodedata.combining(c))
    return " ".join(t.split())


def name_groups(names: dict[str, str], username: str = "") -> dict[str, tuple[str, list[str]]]:
    """Nomi trovati sui profili, uniti quando coincidono (co-occorrenze): {chiave: (nome, [siti])}.
    Si saltano i nomi uguali all'username, che non aggiungono niente."""
    user = "".join(norm_name(username).split())
    out: dict[str, tuple[str, list[str]]] = {}
    seen: dict[str, dict[str, int]] = {}
    for site, raw in names.items():
        key = norm_name(raw)
        if not key or "".join(key.split()) == user:
            continue
        out.setdefault(key, ("", []))[1].append(site)
        variants = seen.setdefault(key, {})
        variants[raw.strip()] = variants.get(raw.strip(), 0) + 1
    return {k: (max(seen[k], key=lambda v: (seen[k][v], -len(v))), sorted(sites)) for k, (_l, sites) in out.items()}


def build_graph(report: IdentityReport, names: dict[str, str] | None = None
                ) -> tuple[list[Node], list[Edge], set[str]]:
    """Dal rapporto di correlazione ai nodi/archi del grafo. Ritorna anche i gruppi da chiudere.
    `names`: nome visualizzato trovato su ogni sito; ogni nome diventa un nodo, collegato a tutti gli
    account in cui compare."""
    nodes: list[Node] = []
    edges: list[Edge] = []
    collapse: set[str] = set()
    user = Node("user", "user", report.username or "?", TARGET_BORDER, size=KIND_SIZE["user"],
                info={"uniqueness": report.username_uniqueness})
    nodes.append(user)
    by_cluster: dict[int, list] = {}
    for a in report.accounts:
        by_cluster.setdefault(a.cluster, []).append(a)
    has_seed = any(SEED in (e.a, e.b) for e in report.edges)
    if has_seed:
        nodes.append(Node("seed", "seed", tr("indizi noti"), SEED_COLOR, size=KIND_SIZE["seed"], parent="user"))
        edges.append(Edge("user", "seed", 1.0, SEED_COLOR))
    for idx in sorted(by_cluster, key=lambda i: (i < 0, i)):
        accs = by_cluster[idx]
        gid = f"group:{idx}"
        col = cluster_color(idx)
        if idx == 0:
            label = tr("Identità principale")
        elif idx > 0:
            label = tr("Gruppo {n}", n=idx + 1)
        else:
            label = tr("Senza legami")
        nodes.append(Node(gid, "group", f"{label} ({len(accs)})", col, size=KIND_SIZE["group"], parent="user",
                          info={"cluster": idx, "count": len(accs)}))
        edges.append(Edge("user", gid, 1.0 + len(accs), col))
        if idx < 0 and len(accs) > COLLAPSE_OVER:
            collapse.add(gid)
        for a in accs:
            nid = "acc:" + a.site
            nodes.append(Node(nid, "account", a.site, col, glyph=_initials(a.site), size=KIND_SIZE["account"],
                              parent=gid, info={"site": a.site, "confidence": a.confidence, "url": a.url}))
            edges.append(Edge(gid, nid, 0.5 + a.confidence / 100, col))
    # nomi trovati sui profili: un nodo per nome, appeso all'account più sicuro, legato agli altri
    conf = {a.site: a.confidence for a in report.accounts}
    for key, (label, sites) in name_groups(names or {}, report.username).items():
        sites = [x for x in sites if x in conf]
        if not sites:
            continue
        sites.sort(key=lambda x: -conf[x])
        nid = "name:" + key
        nodes.append(Node(nid, "name", label, NAME_COLOR, glyph=_initials(label), size=KIND_SIZE["name"],
                          parent="acc:" + sites[0], info={"sites": sites}))
        for i, site in enumerate(sites):
            edges.append(Edge("acc:" + site, nid, 0.6, NAME_COLOR, tree=i == 0))
    # indizi fra account (e con gli indizi noti): pieni se sopra soglia, tratteggiati se deboli
    ev = sorted((e for e in report.edges if e.score >= MIN_EDGE), key=lambda e: -e.score)[:MAX_EVIDENCE_EDGES]
    for e in ev:
        a = "seed" if e.a == SEED else "acc:" + e.a
        b = "seed" if e.b == SEED else "acc:" + e.b
        strong = e.score >= LINK_THRESHOLD
        top = max(e.evidence, key=lambda x: x.weight).signal if e.evidence else ""
        edges.append(Edge(a, b, e.score * 3, STRONG_COLOR if strong else WEAK_COLOR, dashed=not strong,
                          tree=False, label=f"{tr(top)} {int(e.score * 100)}%"))
    return nodes, edges, collapse


class GraphView(ctk.CTkFrame):
    def __init__(self, parent, fonts: theme.Fonts, on_select: Callable[[str | None], None] | None = None,
                 on_open: Callable[[str | None], None] | None = None):
        super().__init__(parent, fg_color=theme.PANEL, corner_radius=0)
        self.fonts = fonts
        self.on_select, self.on_open = on_select, on_open
        self.report: IdentityReport | None = None
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str], Edge] = {}
        self.adj: dict[str, set[str]] = {}
        self.collapsed: set[str] = set()
        self.hidden: set[str] = set()
        self.selected: str | None = None
        self.active = True
        self.scale = 1.0
        self.cx = self.cy = 0.0
        self._lock = threading.Lock()
        self._hot = 0.0
        self._drag: tuple | None = None
        self._moved = 0
        self._fit_pending = True
        self._dirty = True
        self._stats_text = ""
        self.layout = "tree"           # all'apertura: gerarchico
        self._avatars: dict[str, str] = {}          # sito -> miniatura dell'avatar (avatars.py)
        self._avatar_imgs: dict[tuple, object] = {}
        self._user_view = False
        self._untangling = False
        self._ly_btns: dict[str, ctk.CTkButton] = {}
        self.spread = 1.0                   # distanza fra i nodi (pulsanti Distanzia / Avvicina)
        self.hide_loose = False             # nasconde il gruppo "senza legami"
        self._rank: dict[str, float] = {}   # ordine dei fratelli scelto da "Riduci incroci" (layout statici)

        self.canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0, bd=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        c = self.canvas
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._motion)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Double-Button-1>", self._double)
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.2))
        c.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 1.2))
        c.bind("<Configure>", lambda _e: self._on_resize())
        c.bind("<Motion>", self._hover)
        c.bind("<Leave>", lambda _e: (c.delete("tip"), setattr(self, "_tip_id", None)), add="+")
        self._tip_id: str | None = None
        self._build_tools()
        threading.Thread(target=self._layout_loop, daemon=True, name="GraphLayout").start()
        self.after(60, self._frame)

    # ---------------------------------------------------------------- strumenti
    def _build_tools(self) -> None:
        box = ctk.CTkFrame(self, fg_color=theme.PANEL, corner_radius=4, border_width=1, border_color=theme.BORDER)
        box.place(relx=1.0, x=-12, y=12, anchor="ne")
        for glyph, cmd in (("+", lambda: self._zoom_center(1.25)), ("−", lambda: self._zoom_center(0.8)),
                           ("⌂", self._fit_auto), ("⟳", self.relayout)):
            ctk.CTkButton(box, text=glyph, width=28, height=26, corner_radius=3, fg_color="transparent",
                          hover_color=theme.HOVER, text_color=theme.TEXT_SOFT, font=(theme.mono_font(), 14),
                          command=cmd).pack(padx=3, pady=2)
        self.tools = box
        self.legend = ctk.CTkLabel(self, text="", font=self.fonts.small, text_color=theme.TEXT_DIM, justify="left",
                                   fg_color=theme.PANEL, corner_radius=4, padx=10, pady=6)
        self._build_layout_box()
        from .widgets import ScaleRow
        self.scale_row = ScaleRow(self, self.fonts, [
            (CLUSTER_COLORS[0], tr("identità principale")), (CLUSTER_COLORS[1], tr("altri gruppi")),
            (LOOSE_COLOR, tr("senza legami")), (SEED_COLOR, tr("indizi noti")), (NAME_COLOR, tr("nomi trovati")),
            (STRONG_COLOR, tr("— indizio forte")), (WEAK_COLOR, tr("┄ indizio debole (clic sul nodo)"))], rows=2)
        self.scale_row.configure(fg_color=theme.PANEL, corner_radius=4)
        self.scale_row.place(relx=1.0, x=-12, rely=1.0, y=-12, anchor="se")
        self.empty = ctk.CTkLabel(self, text=tr("Avvia una ricerca: qui compare il grafo delle identità."),
                                  font=self.fonts.body, text_color=theme.TEXT_DIM, fg_color=theme.BG)
        self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def _build_layout_box(self) -> None:
        box = ctk.CTkFrame(self, fg_color=theme.PANEL, corner_radius=4, border_width=1, border_color=theme.BORDER)
        box.place(x=12, rely=1.0, y=-12, anchor="sw")
        ctk.CTkLabel(box, text=tr("Layout").upper(), font=(theme.ui_font(), 11, "bold"), text_color=theme.OK,
                     anchor="w").grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 2))
        names = (("organic", tr("Organico")), ("circle", tr("Circolare")), ("tree", tr("Gerarchico")),
                 ("grid", tr("Griglia")))
        for i, (key, label) in enumerate(names):
            b = ctk.CTkButton(box, text=label, width=96, height=28, corner_radius=4, font=self.fonts.small,
                              fg_color=theme.PANEL_3, hover_color=theme.HOVER, text_color=theme.TEXT,
                              command=lambda k=key: self.set_layout(k))
            b.grid(row=1 + i // 2, column=i % 2, padx=(8 if i % 2 == 0 else 3, 3 if i % 2 == 0 else 8), pady=3,
                   sticky="ew")
            self._ly_btns[key] = b
        self.btn_untangle = ctk.CTkButton(box, text="✂ " + tr("Riduci incroci"), height=28, corner_radius=4,
                                          font=self.fonts.small, fg_color=theme.PANEL_3, hover_color=theme.HOVER,
                                          text_color=theme.TEXT, command=self.untangle)
        self.btn_untangle.grid(row=3, column=0, columnspan=2, padx=8, pady=3, sticky="ew")
        for i, (text, f) in enumerate((("⇔ " + tr("Distanzia"), 1.25), ("⇆ " + tr("Avvicina"), 0.8))):
            ctk.CTkButton(box, text=text, width=96, height=28, corner_radius=4, font=self.fonts.small,
                          fg_color=theme.PANEL_3, hover_color=theme.HOVER, text_color=theme.TEXT,
                          command=lambda k=f: self.set_spread(self.spread * k)).grid(
                row=4, column=i, padx=(8, 3) if i == 0 else (3, 8), pady=3, sticky="ew")
        self.btn_loose = ctk.CTkButton(box, text="", height=28, corner_radius=4, font=self.fonts.small,
                                       fg_color=theme.PANEL_3, hover_color=theme.HOVER, text_color=theme.TEXT,
                                       command=self.toggle_loose)
        self.btn_loose.grid(row=5, column=0, columnspan=2, padx=8, pady=(3, 8), sticky="ew")
        self._paint_loose_btn()
        self.layout_box = box
        self._paint_layout_btns()

    def _paint_layout_btns(self) -> None:
        for k, b in self._ly_btns.items():
            on = k == self.layout
            b.configure(fg_color=theme.OK if on else theme.PANEL_3, text_color="#0b1a12" if on else theme.TEXT,
                        hover_color=theme.OK if on else theme.HOVER)

    def _paint_loose_btn(self) -> None:
        on = self.hide_loose
        self.btn_loose.configure(text=("◉ " + tr("Mostra senza legami")) if on else ("○ " + tr("Nascondi senza legami")),
                                 fg_color=theme.ACCENT_BG if on else theme.PANEL_3,
                                 text_color=theme.ACCENT if on else theme.TEXT)

    def toggle_loose(self) -> None:
        self.hide_loose = not self.hide_loose
        self._paint_loose_btn()
        with self._lock:
            self._update_hidden()
            if self.layout == "organic":
                self._place_new()
                self._hot = max(self._hot, 0.5)
            else:
                self._apply_static()
        if not self._user_view:
            self._fit_pending = True
        self._dirty = True

    def set_spread(self, value: float) -> None:
        """Distanzia / Avvicina: cambia la distanza fra i nodi in tutti i layout."""
        value = max(0.5, min(4.0, value))
        if abs(value - self.spread) < 1e-6:
            return
        f = value / self.spread
        self.spread = value
        with self._lock:
            if self.layout == "organic":
                for n in self.nodes.values():      # si allarga subito, poi le forze rifiniscono
                    n.x *= f
                    n.y *= f
                self._hot = max(self._hot, 0.3)
            else:
                self._apply_static()
        if not self._user_view:
            self._fit_pending = True
        self._dirty = True

    def set_legend(self, text: str) -> None:
        self._stats_text = text
        self.legend.configure(text=text)
        if text:
            self.legend.place(x=12, y=12, anchor="nw")
        else:
            self.legend.place_forget()

    def set_legend_note(self, text: str) -> None:
        self.legend.configure(text=(self._stats_text + "\n" if self._stats_text else "") + text)

    # ---------------------------------------------------------------- dati
    def set_report(self, report: IdentityReport | None, names: dict[str, str] | None = None) -> None:
        """Nuovo rapporto: conserva le posizioni dei nodi che c'erano già (la ricerca cresce).
        `names`: {sito: nome visualizzato} degli account trovati, per i nodi dei nomi."""
        self.report = report
        if report is None or not report.accounts:
            self.clear()
            self.set_legend("")
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
            return
        self.empty.place_forget()
        nodes, edges, collapse = build_graph(report, names)
        with self._lock:
            old = self.nodes
            new: dict[str, Node] = {}
            changed = len(nodes) != len(old)
            for n in nodes:
                o = old.get(n.id)
                if o is not None:
                    o.label, o.color, o.size, o.info, o.glyph = n.label, n.color, n.size, n.info, n.glyph
                    if o.parent != n.parent:              # l'account è passato in un altro gruppo
                        o.parent, o.fixed, o.placed, changed = n.parent, False, False, True
                    new[n.id] = o
                else:
                    new[n.id] = n
                    changed = True
            self.nodes = new
            self.edges = {}
            for e in edges:
                if e.a in new and e.b in new and (e.a, e.b) not in self.edges and (e.b, e.a) not in self.edges:
                    self.edges[(e.a, e.b)] = e
            adj: dict[str, set[str]] = {i: set() for i in new}
            for a, b in self.edges:
                adj[a].add(b)
                adj[b].add(a)
            self.adj = adj
            for gid in collapse:
                if gid not in old:
                    self.collapsed.add(gid)
            self.collapsed &= set(new)
            self._place_new()
            self._update_hidden()
            if changed and self.layout != "organic":
                self._apply_static()
            elif changed:
                self._hot = max(self._hot, 1.0 if len(old) == 0 else 0.35)
            if changed and not self._user_view:
                self._fit_pending = True
            if self.selected and self.selected not in new:
                self.selected = None
        main = len(report.clusters[0]) if report.clusters else 0
        self.set_legend(f"{report.username}  ·  {len(report.accounts)} {tr('account')}  ·  "
                        f"{len(report.clusters)} {tr('gruppi')}\n"
                        f"{tr('identità principale')}: {main}  ·  {tr('unicità')} {int(report.username_uniqueness * 100)}%")
        self._dirty = True

    def select(self, site: str | None) -> None:
        nid = "acc:" + site if site else None
        self.selected = nid if nid in self.nodes else None
        if self.selected and self.nodes[self.selected].id in self.hidden:
            p = self.nodes[self.selected].parent
            if p:
                with self._lock:
                    self.collapsed.discard(p)
                    self._update_hidden()
                    self._hot = max(self._hot, 0.4)
        self._dirty = True

    def _place_new(self) -> None:
        """I nodi nuovi partono vicino al padre già disposto (o intorno al centro)."""
        for n in sorted(self.nodes.values(), key=lambda n: LEVEL.get(n.kind, 9)):
            if n.placed:
                continue
            ang = random.random() * 6.283
            anchor = self.nodes.get(n.parent) if n.parent else None
            if anchor is None or not anchor.placed:
                anchor = next((self.nodes[m] for m in self.adj.get(n.id, ()) if self.nodes[m].placed), None)
            if n.kind == "user":
                n.x, n.y, n.fixed = 0.0, 0.0, True
            elif anchor is not None:
                dist = IDEAL * (1.4 if n.kind == "group" else 0.8)
                n.x, n.y = anchor.x + math.cos(ang) * dist, anchor.y + math.sin(ang) * dist
            else:
                n.x, n.y = math.cos(ang) * IDEAL * 2, math.sin(ang) * IDEAL * 2
            n.placed = True

    def _update_hidden(self) -> None:
        loose = "group:-1"
        hidden = {n.id for n in self.nodes.values()
                  if n.parent in self.collapsed
                  or (self.hide_loose and (n.id == loose or n.parent == loose))}
        # un nome resta visibile finché almeno uno dei suoi account si vede
        for n in self.nodes.values():
            if n.kind == "name":
                if all("acc:" + x in hidden for x in n.info.get("sites", [])):
                    hidden.add(n.id)
                elif n.parent in hidden:
                    vis = next(("acc:" + x for x in n.info.get("sites", []) if "acc:" + x not in hidden), None)
                    if vis:
                        n.parent, n.placed = vis, n.placed
        self.hidden = hidden

    def set_avatars(self, thumbs: dict[str, str]) -> None:
        """Miniature degli avatar da mostrare dentro i nodi degli account."""
        self._avatars = dict(thumbs)
        self._avatar_imgs.clear()
        self._dirty = True

    def _avatar_img(self, site: str, r: float, dim: bool):
        path = self._avatars.get(site)
        if not path:
            return None
        d = max(8, int(round(r * 2)))
        key = (site, d, dim)
        img = self._avatar_imgs.get(key)
        if img is None:
            try:
                from PIL import Image, ImageDraw
                big = d * 3                                  # disegnato più grande e ridotto: bordo liscio
                with Image.open(path) as src:
                    face = src.convert("RGB").resize((big, big), Image.Resampling.LANCZOS)
                if dim:
                    face = Image.blend(face, Image.new("RGB", face.size, theme.BG), 0.7)
                mask = Image.new("L", (big, big), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
                face.putalpha(mask)
                img = theme.photo(face.resize((d, d), Image.Resampling.LANCZOS), self.canvas)
            except Exception:  # noqa: BLE001 - miniatura illeggibile: si torna alle iniziali
                self._avatars.pop(site, None)
                return None
            if len(self._avatar_imgs) > 600:
                self._avatar_imgs.clear()
            self._avatar_imgs[key] = img
        return img

    def clear(self) -> None:
        with self._lock:
            self.nodes, self.edges, self.adj = {}, {}, {}
            self.collapsed, self.hidden = set(), set()
            self._rank = {}
            self.selected = None
        self._fit_pending = True
        self._user_view = False
        self._dirty = True

    # ---------------------------------------------------------------- layout (thread)
    def _layout_loop(self) -> None:
        while True:
            if self._hot <= 0.01 or not self.active:
                time.sleep(0.05)
                continue
            with self._lock:
                self._iterate(self._hot)
                self._hot *= 0.97
                self._dirty = True
                if self._hot <= 0.01 and not self._user_view:
                    self._fit_pending = True
            time.sleep(0.01)

    def _iterate(self, temp: float) -> None:
        nodes = [n for n in self.nodes.values() if n.id not in self.hidden]
        if len(nodes) < 2:
            return
        disp = {n.id: [0.0, 0.0] for n in nodes}
        sp = self.spread
        sp2, far = sp * sp, 360000 * sp * sp
        for i, a in enumerate(nodes):
            ax, ay, da = a.x, a.y, disp[a.id]
            for b in nodes[i + 1:]:
                dx, dy = ax - b.x, ay - b.y
                d2 = dx * dx + dy * dy + 0.01
                if d2 > far:
                    continue
                f = REPULSION * sp2 / d2 * ((a.size + b.size) / 60.0)
                d = math.sqrt(d2)
                fx, fy = dx / d * f, dy / d * f
                da[0] += fx
                da[1] += fy
                db = disp[b.id]
                db[0] -= fx
                db[1] -= fy
        for (ia, ib), e in self.edges.items():
            if ia in self.hidden or ib in self.hidden or e.dashed:
                continue                            # gli indizi deboli non spostano i nodi
            a, b = self.nodes[ia], self.nodes[ib]
            dx, dy = a.x - b.x, a.y - b.y
            d = math.sqrt(dx * dx + dy * dy) + 0.01
            if e.tree:
                ideal, k = (IDEAL + (a.size + b.size) * 0.4) * sp, 0.08
                if "user" in (ia, ib):                 # i gruppi stanno lontani dall'username: rami ben separati
                    ideal *= 1.9
            else:                                   # gli indizi avvicinano, ma meno della struttura
                ideal, k = IDEAL * 0.9 * sp, 0.03 * min(1.0, e.weight / 3 + 0.2)
            f = (d - ideal) * k
            fx, fy = dx / d * f, dy / d * f
            disp[ia][0] -= fx
            disp[ia][1] -= fy
            disp[ib][0] += fx
            disp[ib][1] += fy
        step = 12.0 * temp + 0.5
        for n in nodes:
            if n.fixed:
                continue
            vx, vy = disp[n.id]
            vx -= n.x * GRAVITY
            vy -= n.y * GRAVITY
            v = math.sqrt(vx * vx + vy * vy)
            if v > step:
                vx, vy = vx / v * step, vy / v * step
            n.x += vx
            n.y += vy
            rmin = RADIUS.get(n.kind, 0.0) * sp         # i rami stanno fuori dall'username (centro)
            rr = math.hypot(n.x, n.y)
            if 0.01 < rr < rmin:
                n.x, n.y = n.x / rr * rmin, n.y / rr * rmin

    def _fit_auto(self) -> None:
        self._user_view = False
        self.fit()

    def relayout(self) -> None:
        self._user_view = False
        with self._lock:
            if self.layout != "organic":
                self._apply_static()
            else:
                for n in self.nodes.values():
                    if n.kind == "user":
                        n.x = n.y = 0.0
                        n.fixed = True
                    else:
                        n.fixed = False
                        n.placed = False
                self._place_new()
                self._hot = 1.0
        self._fit_pending = True
        self._dirty = True

    # ---------------------------------------------------------------- layout statici
    def set_layout(self, kind: str) -> None:
        if kind not in LAYOUTS or self._untangling:
            return
        self.layout = kind
        self._paint_layout_btns()
        self.relayout()

    def _visible(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.id not in self.hidden]

    def _order(self) -> list[Node]:
        """Nodi visibili in ordine di albero (username, poi i rami): tiene vicini i parenti."""
        nodes = {n.id: n for n in self._visible()}
        kids: dict[str | None, list[Node]] = {}
        for n in nodes.values():
            p = n.parent if n.parent in nodes else None
            kids.setdefault(p, []).append(n)
        for lst in kids.values():
            lst.sort(key=lambda n: (LEVEL.get(n.kind, 9), self._rank.get(n.id, 0.0),
                                    n.info.get("cluster", 0) < 0, n.info.get("cluster", 0),
                                    -n.info.get("confidence", 0), n.label.lower()))
        out: list[Node] = []

        def walk(p):
            for n in kids.get(p, []):
                out.append(n)
                walk(n.id)
        walk(None)
        return out

    def _apply_static(self) -> None:
        nodes = self._order()
        self._hot = 0.0
        if not nodes:
            return
        for n in nodes:
            n.fixed, n.placed = True, True
        if self.layout == "circle":
            user = [n for n in nodes if n.kind == "user"]
            ring = [n for n in nodes if n.kind != "user"]
            for n in user:
                n.x = n.y = 0.0
            k = max(1, len(ring))
            r = max(IDEAL * 1.5, IDEAL * k / 6.0)
            for i, n in enumerate(ring):
                a = -math.pi / 2 + 2 * math.pi * i / k
                n.x, n.y = r * math.cos(a), r * math.sin(a)
        elif self.layout == "grid":
            k = len(nodes)
            cols = max(1, int(math.ceil(math.sqrt(k))))
            step = IDEAL * 1.1
            for i, n in enumerate(nodes):
                n.x = (i % cols - (cols - 1) / 2) * step
                n.y = (i // cols - (math.ceil(k / cols) - 1) / 2) * step
        elif self.layout == "tree":
            self._tree_layout(nodes)
        if self.spread != 1.0:
            for n in nodes:
                n.x *= self.spread
                n.y *= self.spread
        self._dirty = True

    def _tree_layout(self, nodes: list[Node]) -> None:
        ids = {n.id for n in nodes}
        kids: dict[str | None, list[Node]] = {}
        for n in nodes:
            kids.setdefault(n.parent if n.parent in ids else None, []).append(n)
        sx, sy = IDEAL * 1.15, IDEAL * 1.3
        cursor = [0.0]

        def place(n: Node, depth: int) -> None:
            ch = kids.get(n.id, [])
            n.y = depth * sy
            if not ch:
                n.x = cursor[0]
                cursor[0] += sx
                return
            if len(ch) > TREE_WRAP and not any(kids.get(c.id) for c in ch):
                cols = max(6, int(math.ceil(math.sqrt(len(ch) * 1.6))))
                bx = sx * 1.1
                x0 = cursor[0]
                for i, c in enumerate(ch):
                    c.x = x0 + (i % cols) * bx
                    c.y = (depth + 1) * sy + (i // cols) * sy * 0.62
                cursor[0] = x0 + min(cols, len(ch)) * bx
                n.x = x0 + (min(cols, len(ch)) - 1) * bx / 2
                return
            for c in ch:
                place(c, depth + 1)
            n.x = (ch[0].x + ch[-1].x) / 2
        for r in kids.get(None, []):
            place(r, 0)
            cursor[0] += sx * 0.5
        xs = [n.x for n in nodes]
        off = (min(xs) + max(xs)) / 2
        for n in nodes:
            n.x -= off
            n.y -= sy * 1.5

    # ---------------------------------------------------------------- riduci incroci
    @staticmethod
    def _crosses(p, q) -> bool:
        if p[4] in (q[4], q[5]) or p[5] in (q[4], q[5]):
            return False

        def sgn(ax, ay, bx, by, cx, cy):
            v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            return (v > 0) - (v < 0)
        d1 = sgn(p[0], p[1], p[2], p[3], q[0], q[1])
        d2 = sgn(p[0], p[1], p[2], p[3], q[2], q[3])
        d3 = sgn(q[0], q[1], q[2], q[3], p[0], p[1])
        d4 = sgn(q[0], q[1], q[2], q[3], p[2], p[3])
        return d1 != d2 and d3 != d4

    def count_crossings(self) -> int:
        segs = []
        for (a, b), e in self.edges.items():
            if a in self.hidden or b in self.hidden or e.dashed:
                continue
            na, nb = self.nodes[a], self.nodes[b]
            segs.append((na.x, na.y, nb.x, nb.y, a, b))
        c = 0
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                if self._crosses(segs[i], segs[j]):
                    c += 1
        return c

    def _untangle_static(self) -> None:
        """Gerarchico / Circolare / Griglia: riordina i fratelli (metodo del baricentro) e tiene
        l'ordine con meno archi incrociati. La disposizione resta quella scelta."""
        with self._lock:
            best_c = self.count_crossings()
            best_rank = dict(self._rank)
            tries = 0
            for _ in range(24):
                tries += 1
                pos = {n.id: n.x if self.layout == "tree" else math.atan2(n.y, n.x) for n in self._visible()}
                key: dict[str, float] = {}
                for nid in pos:
                    nb = [pos[m] for m in self.adj.get(nid, ()) if m in pos
                          and not (self.edges.get((nid, m)) or self.edges.get((m, nid))).tree]
                    if nb:
                        key[nid] = sum(nb) / len(nb)
                kids: dict[str, list[str]] = {}
                for nid in pos:
                    p = self.nodes[nid].parent
                    if p:
                        kids.setdefault(p, []).append(nid)
                for p, ch in kids.items():      # un gruppo segue la media dei suoi account
                    ks = [key[c] for c in ch if c in key]
                    if ks and p not in key:
                        key[p] = sum(ks) / len(ks)
                rank = {}
                for p, ch in kids.items():
                    ch.sort(key=lambda c: (key.get(c, pos[c]), pos[c]))
                    for i, c in enumerate(ch):
                        rank[c] = float(i)
                if rank == self._rank:
                    break
                self._rank = rank
                self._apply_static()
                c = self.count_crossings()
                if c < best_c:
                    best_c, best_rank = c, dict(rank)
                if best_c == 0:
                    break
            self._rank = best_rank
            self._apply_static()
        self._dirty = True
        self.set_legend_note(tr("{n} incroci dopo {k} tentativi", n=best_c, k=tries))

    def untangle(self) -> None:
        if self._untangling:
            return
        if self.layout != "organic":
            self._untangle_static()
            return
        self._untangling = True
        self._paint_layout_btns()
        self.btn_untangle.configure(text="✂ …", state="disabled")
        threading.Thread(target=self._untangle_run, daemon=True, name="Untangle").start()

    def _untangle_run(self) -> None:
        best, best_c, k = None, 1 << 30, 0
        t0 = time.monotonic()
        try:
            with self._lock:
                if len(self._visible()) < 3:
                    return
                self._hot = 0.0
                while k < UNTANGLE_RUNS and time.monotonic() - t0 < UNTANGLE_BUDGET:
                    for n in self.nodes.values():
                        if n.kind == "user":
                            n.x, n.y, n.fixed = 0.0, 0.0, True
                        else:
                            n.fixed, n.placed = False, False
                    self._place_new()
                    temp = 1.0
                    for _ in range(400):
                        self._iterate(temp)
                        temp *= 0.985
                    k += 1
                    c = self.count_crossings()
                    if c < best_c:
                        best_c, best = c, {n.id: (n.x, n.y) for n in self.nodes.values()}
                    if best_c == 0:
                        break
                if best:
                    for nid, (x, y) in best.items():
                        if nid in self.nodes:
                            self.nodes[nid].x, self.nodes[nid].y = x, y
                self._hot = 0.0
                self._dirty = True
        finally:
            self._untangling = False
            self._fit_pending = True

            def done():
                try:
                    self.btn_untangle.configure(text="✂ " + tr("Riduci incroci"), state="normal")
                    self.set_legend_note(tr("{n} incroci dopo {k} tentativi", n=best_c if best else 0, k=k))
                except tk.TclError:
                    pass
            self.after(0, done)

    # ---------------------------------------------------------------- vista
    def _on_resize(self) -> None:
        if not self._user_view:
            self._fit_pending = True
        self._dirty = True

    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return (x - self.cx) * self.scale + w / 2, (y - self.cy) * self.scale + h / 2

    def _to_world(self, sx: float, sy: float) -> tuple[float, float]:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return (sx - w / 2) / self.scale + self.cx, (sy - h / 2) / self.scale + self.cy

    def fit(self) -> None:
        with self._lock:
            pts = [(n.x, n.y) for n in self.nodes.values() if n.id not in self.hidden]
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if not pts or w < 10:
            return
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        wx, wy = max(xs) - min(xs), max(ys) - min(ys)
        best = None
        for x0, y0, x1, y1 in self._free_rects(w, h):   # la zona libera dove il disegno viene più grande
            sc = max(0.15, min(1.6, min((x1 - x0 - 130) / max(wx, 1.0), (y1 - y0 - 90) / max(wy, 1.0))))
            if best is None or sc > best[0]:
                best = (sc, x0, y0, x1, y1)
        self.scale, x0, y0, x1, y1 = best
        # il centro del disegno va al centro della zona libera, non sotto i riquadri sovrapposti
        self.cx = (min(xs) + max(xs)) / 2 - ((x0 + x1) / 2 - w / 2) / self.scale
        self.cy = (min(ys) + max(ys)) / 2 - ((y0 + y1) / 2 - h / 2) / self.scale
        self._fit_pending = False
        self._dirty = True

    def _free_rects(self, w: int, h: int) -> list[tuple[int, int, int, int]]:
        """Le parti del canvas non coperte dai riquadri: a destra di Layout, oppure sopra Layout."""
        def size(wd):
            try:
                return (wd.winfo_width(), wd.winfo_height()) if wd.winfo_ismapped() else (0, 0)
            except Exception:  # noqa: BLE001
                return 0, 0
        lw, lh = size(self.layout_box)
        tw = size(self.tools)[0]
        gh = size(self.legend)[1]
        sh = size(self.scale_row)[1]
        top = gh + 20 if gh else 0
        right = w - (tw + 24 if tw else 0)
        rects = [(lw + 24 if lw else 0, top, right, h - (sh + 20 if sh else 0)),
                 (0, top, right, h - (lh + 24 if lh else 0))]
        rects = [r for r in rects if r[2] - r[0] >= w * 0.3 and r[3] - r[1] >= h * 0.25]
        return rects or [(0, 0, w, h)]

    def _zoom_center(self, f: float) -> None:
        self._zoom_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, f)

    def _zoom_at(self, sx: float, sy: float, f: float) -> None:
        self._user_view = True
        wx, wy = self._to_world(sx, sy)
        self.scale = max(0.1, min(4.0, self.scale * f))
        nx, ny = self._to_world(sx, sy)
        self.cx += wx - nx
        self.cy += wy - ny
        self._dirty = True

    def _wheel(self, e) -> None:
        self._zoom_at(e.x, e.y, 1.2 if e.delta > 0 else 1 / 1.2)

    # ---------------------------------------------------------------- mouse
    def _node_at(self, sx: float, sy: float) -> Node | None:
        best, bd = None, 1e9
        with self._lock:
            for n in self.nodes.values():
                if n.id in self.hidden:
                    continue
                x, y = self._to_screen(n.x, n.y)
                r = n.size * self.scale / 2 + 4
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d <= r * r and d < bd:
                    best, bd = n, d
        return best

    def _press(self, e) -> None:
        n = self._node_at(e.x, e.y)
        self._drag = ("node", n, e.x, e.y) if n else ("pan", None, e.x, e.y)
        self._moved = 0

    def _motion(self, e) -> None:
        if not self._drag:
            return
        kind, n, lx, ly = self._drag
        dx, dy = e.x - lx, e.y - ly
        self._moved += abs(dx) + abs(dy)
        self._drag = (kind, n, e.x, e.y)
        if kind == "node":
            with self._lock:
                n.x += dx / self.scale
                n.y += dy / self.scale
                n.fixed = True
                if self.layout == "organic":
                    self._hot = max(self._hot, 0.25)
        else:
            self.cx -= dx / self.scale
            self.cy -= dy / self.scale
            self._user_view = True
        self._dirty = True

    def _release(self, e) -> None:
        if not self._drag:
            return
        _kind, n, _x, _y = self._drag
        self._drag = None
        if self._moved > 4:
            return
        self.selected = n.id if n else None
        self._dirty = True
        if self.on_select and n is not None and n.kind == "account":
            self.on_select(n.info["site"])

    def _double(self, e) -> None:
        n = self._node_at(e.x, e.y)
        if not n:
            return
        if n.kind == "group":
            with self._lock:
                if n.id in self.collapsed:
                    self.collapsed.discard(n.id)
                else:
                    self.collapsed.add(n.id)
                self._update_hidden()
                if self.layout == "organic":
                    self._place_new()
                    self._hot = max(self._hot, 0.5)
                else:
                    self._apply_static()
            if not self._user_view:
                self._fit_pending = True
            self._dirty = True
        elif n.kind == "account" and self.on_open:
            self.on_open(n.info["site"])

    def _tip_text(self, n: Node) -> str:
        if n.kind == "account":
            lines = [f"{n.label}  ·  {n.info.get('confidence', 0)}%"]
            for nb in sorted(self.adj.get(n.id, ())):
                e = self.edges.get((n.id, nb)) or self.edges.get((nb, n.id))
                if e is not None and not e.tree:
                    other = self.nodes[nb].label
                    lines.append(f"{e.label}  ↔ {other}")
            return "\n".join(lines[:7])
        if n.kind == "group":
            return n.label + "\n" + (tr("doppio clic: apri") if n.id in self.collapsed else tr("doppio clic: chiudi"))
        if n.kind == "user":
            return f"{n.label}  ·  {tr('unicità')} {int(n.info.get('uniqueness', 0) * 100)}%"
        if n.kind == "name":
            sites = n.info.get("sites", [])
            return f"{n.label}  ·  {tr('nome trovato su {n} siti', n=len(sites))}\n" + ", ".join(sites[:8]) + \
                ("…" if len(sites) > 8 else "")
        return n.label

    def _hover(self, e) -> None:
        if self._drag:
            return
        n = self._node_at(e.x, e.y)
        c = self.canvas
        c.configure(cursor="hand2" if n else "fleur")
        tip = n.id if n else None
        if tip == self._tip_id and c.find_withtag("tip"):
            return
        self._tip_id = tip
        c.delete("tip")
        if n is None:
            return
        x, y = self._to_screen(n.x, n.y)
        r = n.size * self.scale / 2
        t = c.create_text(x, y - r - 12, text=self._tip_text(n), anchor="s", fill=theme.TEXT, justify="left",
                          font=(theme.ui_font(), 10, "bold"), tags=("tip",))
        x0, y0, x1, y1 = c.bbox(t)
        dx = max(0, 8 - x0) - max(0, x1 + 8 - c.winfo_width())
        dy = (r * 2 + 30 + (y1 - y0)) if y0 < 8 else 0
        if dx or dy:
            c.move(t, dx, dy)
            x0, y0, x1, y1 = c.bbox(t)
        box = c.create_rectangle(x0 - 7, y0 - 4, x1 + 7, y1 + 4, fill=theme.PANEL, outline=n.color or EDGE_COLOR,
                                 tags=("tip",))
        c.tag_lower(box, t)

    # ---------------------------------------------------------------- disegno
    def _frame(self) -> None:
        try:
            if self.winfo_exists() and self.winfo_ismapped():
                if self._fit_pending and self.nodes and self.canvas.winfo_width() > 10:
                    self.fit()
                if self._dirty:
                    self._dirty = False
                    self.redraw()
        except tk.TclError:
            return
        self.after(50, self._frame)

    def _draw_glyph(self, kind: str, x: float, y: float, r: float, ink: str) -> None:
        """Icone a linee sottili: persona (username), due persone (gruppo), lente (indizi noti)."""
        c = self.canvas
        lw = max(1.0, r / 14)
        if kind == "user":
            hr = r * 0.22
            c.create_oval(x - hr, y - r * 0.42 - hr, x + hr, y - r * 0.42 + hr, outline=ink, width=lw * 1.3)
            c.create_arc(x - r * 0.42, y - r * 0.05, x + r * 0.42, y + r * 0.75, start=0, extent=180,
                         style="arc", outline=ink, width=lw * 1.3)
        elif kind == "group":
            for ox, k in ((-0.2, 0.8), (0.2, 1.0)):
                hr = r * 0.16 * k
                cx = x + r * ox
                c.create_oval(cx - hr, y - r * 0.3 - hr, cx + hr, y - r * 0.3 + hr, outline=ink, width=lw)
                c.create_arc(cx - r * 0.3 * k, y, cx + r * 0.3 * k, y + r * 0.6 * k, start=0, extent=180,
                             style="arc", outline=ink, width=lw)
        elif kind == "seed":
            rr = r * 0.3
            c.create_oval(x - rr - r * 0.1, y - rr - r * 0.1, x + rr - r * 0.1, y + rr - r * 0.1, outline=ink,
                          width=lw * 1.3)
            c.create_line(x + rr * 0.6, y + rr * 0.6, x + r * 0.45, y + r * 0.45, fill=ink, width=lw * 1.8,
                          capstyle="round")

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        with self._lock:
            nodes = {k: v for k, v in self.nodes.items() if k not in self.hidden}
            edges = list(self.edges.values())
            pos = {k: self._to_screen(n.x, n.y) for k, n in nodes.items()}
        sel = self.selected if self.selected in nodes else None
        focus = ({sel} | self.adj.get(sel, set())) if sel else None
        s = self.scale
        wmax = max((e.weight for e in edges), default=1.0) or 1.0
        edge_boxes: list[tuple[float, float, float, float]] = []
        # prima gli archi della struttura, poi gli indizi (sopra)
        # (tra gli indizi, prima quelli del nodo selezionato: le loro etichette hanno la precedenza)
        for e in sorted(edges, key=lambda e: (not e.tree, not (sel and sel in (e.a, e.b)))):
            if e.a not in pos or e.b not in pos:
                continue
            if e.dashed and sel not in (e.a, e.b):
                continue                            # indizi deboli: solo per il nodo selezionato
            (x1, y1), (x2, y2) = pos[e.a], pos[e.b]
            dim = focus is not None and not (e.a in focus and e.b in focus and (e.a == sel or e.b == sel))
            col = _mix(e.color, theme.BG, 0.78 if dim else (0.45 if e.tree else 0.1))
            w = 0.6 + 1.6 * math.log1p(e.weight) / math.log1p(wmax)
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            dx, dy = x2 - x1, y2 - y1
            d = math.hypot(dx, dy) or 1
            bend = 0.12 if e.tree else 0.22
            if self.layout == "organic" or not e.tree:
                ox, oy = -dy / d * min(36, d * bend), dx / d * min(36, d * bend)
            else:
                ox = oy = 0.0
            c.create_line(x1, y1, mx + ox, my + oy, x2, y2, smooth=True, width=max(0.8, w * s), fill=col,
                          capstyle="round", dash=(4, 3) if e.dashed else ())
            touches = bool(sel) and sel in (e.a, e.b)
            if e.label and not e.tree and not dim and (touches or s > 0.8):
                lx, ly = mx + ox * 0.6, my + oy * 0.6
                half = len(e.label) * 3.0 + 4         # testo a 8 pt: ~6 px per carattere
                bb = (lx - half, ly - 8, lx + half, ly + 8)
                # etichette sovrapposte diventano illeggibili: si disegna solo la prima (le altre nel tooltip)
                if not any(bb[0] < q[2] and q[0] < bb[2] and bb[1] < q[3] and q[1] < bb[3]
                                      for q in edge_boxes):
                    edge_boxes.append(bb)
                    c.create_text(lx, ly, text=e.label, font=(theme.ui_font(), 8),
                                  fill=_mix(e.color, "#ffffff", 0.35))
        font_px = max(7, int(10 * min(s, 1.3)))
        labels = []
        for k, n in nodes.items():
            x, y = pos[k]
            r = n.size * s / 2
            dim = focus is not None and k not in focus
            border = n.color or EDGE_COLOR
            bg = TARGET_BG if n.kind == "user" else n.color if n.kind == "name" else NODE_BG
            if dim:
                border, bg = _mix(border, theme.BG, 0.7), _mix(bg, theme.BG, 0.8)
            bw = (2.5 if n.kind in ("user", "group") else 1.5) * max(0.6, min(s, 1.5))
            if k == sel:
                c.create_oval(x - r - 5, y - r - 5, x + r + 5, y + r + 5, outline=theme.ACCENT, width=2, dash=(3, 3))
            c.create_oval(x - r, y - r, x + r, y + r, fill=bg, outline=border, width=bw)
            if k in self.collapsed:
                c.create_oval(x - r - 3, y - r - 3, x + r + 3, y + r + 3, outline=border, width=1)
                c.create_text(x + r * 0.8, y + r * 0.8, text="+", font=(theme.ui_font(), max(7, int(r * 0.5)), "bold"),
                              fill=border)
            ink = _mix(border, "#000000", 0.2) if not dim else border
            if n.kind == "name":
                ink = "#ffffff" if not dim else _mix("#ffffff", theme.BG, 0.6)
            if n.kind in ("user", "group", "seed") and r >= 5:
                self._draw_glyph(n.kind, x, y, r, ink)
            elif n.kind == "account" and r >= 6 and (face := self._avatar_img(n.label, r - bw / 2, dim)) is not None:
                c.create_image(x, y, image=face)
                c.create_oval(x - r, y - r, x + r, y + r, outline=border, width=bw)
            elif n.glyph and r >= 5:
                c.create_text(x, y, text=n.glyph, font=(theme.ui_font(), max(6, int(r * 0.7)), "bold"), fill=ink)
            conf = n.info.get("confidence")
            if n.kind == "account" and conf is not None and r >= 9 and not dim:
                bx, by, br = x + r * 0.78, y - r * 0.78, max(6.0, r * 0.42)
                badge = STRONG_COLOR if conf >= 70 else WEAK_COLOR if conf >= 40 else LOOSE_COLOR
                c.create_oval(bx - br, by - br, bx + br, by + br, fill=badge, outline=theme.BG, width=1)
                c.create_text(bx, by, text=str(conf), font=(theme.ui_font(), max(6, int(br * 0.85)), "bold"),
                              fill="#ffffff")
            prio = 0 if n.kind == "user" else 1 if k == sel else 2 if n.kind in ("group", "seed") else 3
            if not (prio == 3 and s < 0.22):
                labels.append((prio, -n.info.get("confidence", 0), n, x, y + r + 5, dim))
        # etichette: prima le più importanti; quelle che si sovrappongono vengono abbreviate o nascoste
        placed: list[tuple[float, float, float, float]] = []
        labels.sort(key=lambda t: t[:2])
        for prio, _c, n, x, y, dim in labels:
            px = font_px + (2 if n.kind == "user" else 0)
            fnt = (theme.ui_font(), px, "bold")
            full = n.label if len(n.label) <= 24 else n.label[:23] + "…"
            for label in dict.fromkeys((full, full if len(full) <= 12 else full[:11] + "…")):
                half = len(label) * px * 0.36 + 3
                box = (x - half, y, x + half, y + px * 1.5)
                if prio <= 1 or not any(box[0] < q[2] and box[2] > q[0] and box[1] < q[3] and box[3] > q[1]
                                        for q in placed):
                    break
            else:
                continue
            placed.append(box)
            col = theme.TEXT if not dim else theme.TEXT_FAINT
            if n.kind == "user":
                col = "#f59e0b" if not dim else theme.TEXT_FAINT
            elif n.kind in ("group", "name") and not dim:
                col = _mix(n.color, "#ffffff", 0.45)
            for ox, oy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                c.create_text(x + ox, y + oy, text=label, font=fnt, fill=theme.BG, anchor="n")
            c.create_text(x, y, text=label, font=fnt, fill=col, anchor="n")
        self._tip_id = None
