# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Correlazione d'identità: quali account trovati appartengono davvero alla stessa persona?

Per ogni coppia di account si raccolgono indizi pubblici (link incrociati, stesso avatar, nome
visualizzato, bio, località, link in comune) e li si combina in un punteggio 0..1 con un "OR
rumoroso": 1 - prodotto(1 - peso). Le coppie sopra soglia formano gruppi (union-find); il gruppo
più forte, o quello legato agli indizi noti inseriti dall'utente, è l'identità principale.

Ogni punteggio porta con sé la lista dei motivi: il risultato è spiegabile, non una scatola nera.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
import unicodedata
from urllib.parse import urlsplit

from .models import AccountScore, Evidence, Profile, SiteResult, Status

LINK_THRESHOLD = 0.5       # punteggio minimo perché due account finiscano nello stesso gruppo
SEED = "__seed__"          # nodo fittizio con gli indizi noti (nome, località, link)

STOPWORDS = set("""
a ad al alla alle and are as at be by che con da dal de del della di do e ed for from gli i il in is
it la le lo my nel of on or per su the to un una uno with you your me i'm im
""".split())


@dataclass
class Edge:
    a: str
    b: str
    score: float
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class IdentityReport:
    username: str
    username_uniqueness: float
    accounts: list[AccountScore]
    edges: list[Edge]
    clusters: list[list[str]]       # gruppi di siti; clusters[0] è l'identità principale (se c'è)
    main_cluster: int               # -1 se nessun gruppo

    def account(self, site: str) -> AccountScore | None:
        return next((a for a in self.accounts if a.site == site), None)


# ---------------------------------------------------------------- normalizzazione
def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def norm_url(u: str) -> str:
    u = (u or "").strip().lower()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    parts = urlsplit(u)
    host = (parts.hostname or "").removeprefix("www.").removeprefix("m.")
    return host + parts.path.rstrip("/")


def norm_avatar(u: str) -> str:
    """Avatar confrontabile: niente query string (dimensioni), hash Gravatar se presente."""
    u = (u or "").strip()
    m = re.search(r"gravatar\.com/avatar/([0-9a-f]{32,64})", u, re.I)
    if m:
        return "gravatar:" + m.group(1).lower()
    return norm_url(u.split("?", 1)[0])


# immagini predefinite dei siti (silhouette, iniziali, "no avatar"): non dicono niente sulla persona
DEFAULT_AVATAR_RE = re.compile(
    r"default|placeholder|no[-_]?(?:avatar|image|photo|pic)|anonymous|anon[-_.]|blank|missing|silhouette|"
    r"generic|unknown|empty|nophoto|noimage|guest|identicon|mp\.(?:png|jpg)|"
    r"/avatars?/(?:\d{1,2}|[a-z])\.(?:png|jpe?g|gif|svg)$|ui-avatars\.com|avatar\.(?:svg|png)$|\.svg$",
    re.I)
EMPTY_GRAVATAR = {"d41d8cd98f00b204e9800998ecf8427e",
                  "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}   # hash dell'email vuota
SHARED_AVATAR_LIMIT = 4    # la stessa immagine (non Gravatar) su 4+ account diversi: è un'immagine generica


AVATAR_SAME, AVATAR_SIMILAR = 0.12, 0.30    # distanza fra impronte visive: stessa foto / foto simile (ritagliata)
_FP_LEN = 1 + 144 * 2 + 6


def _fp_vector(fp: str) -> list[float]:
    g = bytes.fromhex(fp[1:289])
    m = sum(g) / len(g)
    sd = (sum((x - m) ** 2 for x in g) / len(g)) ** 0.5
    return [(x - m) / (sd if sd > 1 else 1) for x in g]


def avatar_distance(a: str, b: str) -> float | None:
    """Distanza tra due impronte visive (avatars.fingerprint): ~0 stessa foto, >0.6 foto diverse.
    None se non confrontabili o se i colori medi sono lontani (stessa forma, colori diversi)."""
    if not a or not b or len(a) != _FP_LEN or len(b) != _FP_LEN or a[0] != "2" or b[0] != "2":
        return None
    ca = [int(a[289 + i:291 + i], 16) for i in (0, 2, 4)]
    cb = [int(b[289 + i:291 + i], 16) for i in (0, 2, 4)]
    if sum(abs(x - y) for x, y in zip(ca, cb)) > 90:
        return None
    va, vb = _fp_vector(a), _fp_vector(b)
    return sum(abs(x - y) for x, y in zip(va, vb)) / len(va)


def avatar_similarity(d: float) -> int:
    """Distanza -> somiglianza in percentuale, per i dettagli degli indizi."""
    return max(0, min(100, round(100 * (1 - d / 0.6))))


def is_default_avatar(u: str) -> bool:
    raw = (u or "").strip()
    if not raw:
        return True
    n = norm_avatar(raw)
    if n.startswith("gravatar:"):
        return n[9:] in EMPTY_GRAVATAR or bool(re.search(r"[?&](?:d|default)=(?:blank|mp|mm)\b", raw, re.I)
                                                   and re.search(r"[?&]f(?:orcedefault)?=y", raw, re.I))
    return bool(DEFAULT_AVATAR_RE.search(n) or DEFAULT_AVATAR_RE.search(raw.split("?", 1)[0]))


def tokens(s: str) -> set[str]:
    return {t for t in norm_text(s).split() if len(t) > 2 and t not in STOPWORDS}


def username_uniqueness(username: str) -> float:
    """Quanto è raro un username (0..1): lungo, con cifre o separatori => meno omonimi."""
    u = username.lower()
    score = 0.08 + 0.025 * min(len(u), 16)
    if re.search(r"\d", u):
        score += 0.08
    if re.search(r"[._-]", u):
        score += 0.06
    if re.fullmatch(r"[a-z]{1,6}", u):          # parole corte: tantissimi omonimi
        score -= 0.1
    return max(0.05, min(score, 0.6))


# ---------------------------------------------------------------- indizi tra due profili
def _site_refs(r: SiteResult) -> set[str]:
    """Modi in cui un altro profilo potrebbe linkare questo account."""
    refs = {norm_url(r.url)}
    return {x for x in refs if x}


def pair_evidence(a_name: str, a: Profile, a_refs: set[str], b_name: str, b: Profile, b_refs: set[str],
                  username: str, generic_avatars: set[str] | frozenset = frozenset(),
                  generic_hashes: set[str] | frozenset = frozenset()) -> list[Evidence]:
    ev: list[Evidence] = []
    a_links = {norm_url(x) for x in a.links if x}
    b_links = {norm_url(x) for x in b.links if x}

    # 1) link incrociati: il profilo A linka il profilo B (o viceversa) — l'indizio più forte
    if b_refs & a_links:
        ev.append(Evidence("link incrociato", 0.9, f"{a_name} linka {b_name}"))
    if a_refs & b_links:
        ev.append(Evidence("link incrociato", 0.9, f"{b_name} linka {a_name}"))

    # 2) link esterni in comune (stesso sito personale, stesso canale...)
    shared = (a_links & b_links) - a_refs - b_refs
    if shared:
        ev.append(Evidence("link in comune", 0.7, ", ".join(sorted(shared)[:3])))

    # 3) stessa immagine del profilo
    #    prima l'impronta visiva (stessa foto su server diversi), poi l'indirizzo uguale
    ha, hb = a.avatar_hash, b.avatar_hash
    blank = "-" in (ha, hb) or ha in generic_hashes or hb in generic_hashes
    dist = None if blank else avatar_distance(ha, hb)
    av_a, av_b = norm_avatar(a.avatar), norm_avatar(b.avatar)
    if dist is not None and dist <= AVATAR_SAME:
        ev.append(Evidence("stesso avatar", 0.8, f"immagine uguale ({avatar_similarity(dist)}%)"))
    elif dist is not None and dist <= AVATAR_SIMILAR:
        ev.append(Evidence("avatar simile", 0.45, f"immagine simile ({avatar_similarity(dist)}%)"))
    elif (av_a and av_a == av_b and av_a not in generic_avatars and not is_default_avatar(a.avatar)
          and not blank):
        ev.append(Evidence("stesso avatar", 0.8, av_a))

    # 4) nome visualizzato
    na, nb = norm_text(a.display_name), norm_text(b.display_name)
    nu = norm_text(username)
    if na and nb:
        sim = SequenceMatcher(None, na, nb).ratio()
        if na == nb and na.replace(" ", "") == nu.replace(" ", ""):
            ev.append(Evidence("nome = username", 0.1, a.display_name))
        elif sim >= 0.85:
            w = 0.55 if " " in na else 0.35        # nome e cognome valgono più di una parola sola
            ev.append(Evidence("nome simile", round(w * sim, 2), f"{a.display_name} ≈ {b.display_name}"))
        else:
            # nome parziale: "gianna" e "Gianna Harner" (il nome di battesimo è la prima parola dell'altro)
            short, full = (na, nb) if len(na) < len(nb) else (nb, na)
            if " " not in short and len(short) >= 4 and full.split()[0] == short and " " in full:
                in_user = short in nu.replace(" ", "")
                ev.append(Evidence("nome compatibile", 0.5 if in_user else 0.3,
                                   f"{a.display_name} ⊂ {b.display_name}" if short == na else
                                   f"{b.display_name} ⊂ {a.display_name}"))

    # 5) bio
    ta, tb = tokens(a.bio), tokens(b.bio)
    if len(ta) >= 3 and len(tb) >= 3:
        jac = len(ta & tb) / len(ta | tb)
        if jac >= 0.2:
            ev.append(Evidence("bio simile", round(min(0.6, jac), 2), ", ".join(sorted(ta & tb)[:6])))

    # 6) località
    la, lb = norm_text(a.location), norm_text(b.location)
    if la and lb:
        if la == lb:
            ev.append(Evidence("stessa località", 0.25, a.location))
        elif la in lb or lb in la or tokens(la) & tokens(lb):
            ev.append(Evidence("località compatibile", 0.15, f"{a.location} / {b.location}"))
    return ev


def conflicts(a: Profile, b: Profile, username: str) -> list[Evidence]:
    """Indizi contrari: nome o località chiaramente diversi."""
    out: list[Evidence] = []
    na, nb, nu = norm_text(a.display_name), norm_text(b.display_name), norm_text(username)
    if na and nb and nu not in (na, nb) and SequenceMatcher(None, na, nb).ratio() < 0.5:
        out.append(Evidence("nome diverso", -0.5, f"{a.display_name} ≠ {b.display_name}"))
    la, lb = norm_text(a.location), norm_text(b.location)
    if la and lb and la not in lb and lb not in la and not tokens(la) & tokens(lb):
        out.append(Evidence("località diversa", -0.3, f"{a.location} ≠ {b.location}"))
    return out


def combine(evidence: list[Evidence]) -> float:
    p = 1.0
    for e in evidence:
        p *= 1.0 - max(0.0, min(e.weight, 0.99))
    return 1.0 - p


# ---------------------------------------------------------------- correlazione
def correlate(results: list[SiteResult], seed: Profile | None = None) -> IdentityReport:
    found = [r for r in results if r.status == Status.FOUND]
    username = results[0].username if results else ""
    prior = username_uniqueness(username) if username else 0.0
    nodes: dict[str, tuple[Profile, set[str]]] = {r.site: (r.profile, _site_refs(r)) for r in found}
    has_seed = seed is not None and any((seed.display_name, seed.location, seed.links, seed.bio, seed.avatar))
    if has_seed:
        nodes[SEED] = (seed, set())

    names = list(nodes)
    # immagini generiche: uguali su troppi account (o predefinite) -> non contano come "stesso avatar"
    count: dict[str, int] = {}
    for p, _r in nodes.values():
        av = norm_avatar(p.avatar)
        if av:
            count[av] = count.get(av, 0) + 1
    generic = {av for av, k in count.items() if k >= SHARED_AVATAR_LIMIT and not av.startswith("gravatar:")}
    # stessa immagine (per impronta) su troppi account: è l'avatar predefinito di qualche servizio
    hashes = [p.avatar_hash for p, _r in nodes.values() if p.avatar_hash and p.avatar_hash != "-"]

    def near(h: str, k: str) -> bool:
        d = avatar_distance(h, k)
        return d is not None and d <= AVATAR_SAME

    shared = {h for h in hashes if sum(near(h, k) for k in hashes) >= SHARED_AVATAR_LIMIT}
    # ...a meno che gli account che la usano non siano già legati da link: allora è la foto della persona
    generic_hashes = set()
    for h in shared:
        members = [n for n in names if nodes[n][0].avatar_hash and near(h, nodes[n][0].avatar_hash)]
        linked = any(
            e.signal in ("link incrociato", "link in comune")
            for i, a in enumerate(members) for b in members[i + 1:]
            for e in pair_evidence(_label(a), nodes[a][0], nodes[a][1], _label(b), nodes[b][0], nodes[b][1],
                                   username, generic, shared))
        if not linked:
            generic_hashes.add(h)
    edges: list[Edge] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pa, ra = nodes[a]
            pb, rb = nodes[b]
            ev = pair_evidence(_label(a), pa, ra, _label(b), pb, rb, username, generic, generic_hashes)
            if ev:
                edges.append(Edge(a, b, round(combine(ev), 3), ev))

    # gruppi: union-find sugli archi forti
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        if e.score >= LINK_THRESHOLD:
            parent[find(e.a)] = find(e.b)
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)

    def strength(members: list[str]) -> float:
        s = set(members)
        return sum(e.score for e in edges if e.a in s and e.b in s and e.score >= LINK_THRESHOLD)

    multi = [g for g in groups.values() if len(g) > 1]
    seed_group = next((g for g in multi if SEED in g), None) if has_seed else None
    ordered = sorted(multi, key=lambda g: (g is not seed_group, -strength(g), -len(g)))
    clusters = [[m for m in g if m != SEED] for g in ordered]
    clusters = [c for c in clusters if c]
    main = clusters[0] if clusters else []
    cluster_of = {m: i for i, c in enumerate(clusters) for m in c}

    accounts: list[AccountScore] = []
    for r in found:
        idx = cluster_of.get(r.site, -1)
        mine = [e for e in edges if r.site in (e.a, e.b)]
        if idx == 0:
            peers = set(main) | ({SEED} if has_seed else set())
            rel = [e for e in mine if (e.b if e.a == r.site else e.a) in peers]
            conf = 1.0 - (1.0 - prior) * _prod(1.0 - e.score for e in rel)
        elif idx > 0:
            rel = [e for e in mine if (e.b if e.a == r.site else e.a) in clusters[idx]]
            conf = prior * 0.6          # gruppo coerente ma non è l'identità principale
        else:
            rel = mine
            conf = prior if not main else prior * 0.8
        evidence = [Evidence("unicità username", round(prior, 2), username)]
        for e in sorted(rel, key=lambda x: -x.score):
            evidence.extend(e.evidence)
        if idx != 0 and main:
            # confronto con l'identità principale: gli indizi contrari abbassano la confidenza
            against: dict[str, Evidence] = {}
            for m in main + ([SEED] if has_seed else []):
                for c in conflicts(r.profile, nodes[m][0], username):
                    against.setdefault(c.signal, c)
            for c in against.values():
                conf *= 1.0 + c.weight
                evidence.append(c)
        accounts.append(AccountScore(r.site, r.url, int(round(min(conf, 0.99) * 100)), idx, evidence))
    accounts.sort(key=lambda a: (-a.confidence, a.site.lower()))
    return IdentityReport(username, prior, accounts, edges, clusters, 0 if clusters else -1)


def _prod(values) -> float:
    p = 1.0
    for v in values:
        p *= v
    return p


def _label(name: str) -> str:
    return "indizi noti" if name == SEED else name
