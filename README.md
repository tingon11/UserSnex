# UserSnex

Username search across public sites with **identity correlation**: it does not stop at "the username
exists here", it estimates **which accounts really belong to the same person** and with what confidence,
always explaining why.

Graphical interface in the DroidSnex style (dark theme, frameless window, sidebar, counters, tabs,
details and log), plus a command line. A user manual with annotated screenshots is included in nine
languages (`docs/manual/<lang>/MANUAL.md`) and opens from the **📖 Manual** button or with **F1**.

## Getting started

```
pip install -r requirements.txt
python gui.py                    # graphical interface
python gui.py mariorossi88       # start a search right away
python run.py mariorossi88 --html --name "Mario Rossi" --location Milano
python -m pytest                 # tests (offline)
```

Logo and icon: `python tools\make_logo.py` (needs the Panchang typeface installed, as for DroidSnex).
Until `usersnex/gui/assets/logo.png` exists, the header shows the two-colour name as text.

Manual screenshots: `python tools/make_manual.py` regenerates them for every language with made-up
demo data (no real accounts are contacted). It runs only on Linux inside a virtual display (Xvfb), where
the UserSnex window is the only thing on screen, so nothing from your desktop can end up in the images.

## What makes it different

1. **Asynchronous engine**: all sites in parallel (httpx), with a per-host request limit, waiting on
   `Retry-After`, and a one-hour disk cache (`.usersnex_cache/`).
2. **Sites in YAML lists**: `usersnex/sites/usersnex.yaml` (the sites curated by UserSnex, the only list
   in the repository), the WhatsMyName, Sherlock and Maigret lists downloaded into `lists/`, and
   `mytargets.yaml` with the sites you add from the **Config > New resource** form (a site with the same
   name replaces the one from the other lists).
3. **Profile extraction**: display name, bio, location, avatar, links and creation date, from public APIs
   (JSON) or from the page's meta tags (HTML).
4. **Identity score** (`usersnex/correlate.py`). For each pair of accounts:

   | clue | weight |
   |---|---|
   | cross link (A links to profile B) | 90% |
   | same avatar: same photo even on different servers (visual fingerprint) or same Gravatar hash | 80% |
   | similar avatar (same photo cropped or filtered) | 45% |
   | shared external link | 70% |
   | similar bio | up to 60% |
   | similar display name | up to 55% |
   | same / compatible location | 25% / 15% |
   | different name / different location | −50% / −30% |

   Clues are combined with a "noisy OR", pairs above 50% form groups, and the strongest group is the
   **main identity**. **Username uniqueness** counts too (`mariorossi88` has fewer namesakes than
   `mario`).
5. **Known clues**: a name, location or link you already know (sidebar or `--name/--location/--link`)
   act as an anchor: the main identity becomes the group that matches them, and scores are recalculated
   at once, without searching again.
6. **Graph** of accounts and links (hierarchical layout by default), **HTML report**, **JSON / CSV**
   export.

## Sites: more than 10,000

- **usersnex.yaml** (161 sites): 141 found by UserSnex and missing from the other lists (European
  Fediverse, regional forums, marketplaces and blogs from Europe, the Balkans, Russia, North Africa and
  Turkey, Altervista, Italian Wikipedia, ...) plus 20 improved versions of well-known sites, with public
  APIs and profile data for correlation
- **normal list** (~1,100 sites): ~590 from [WhatsMyName](https://github.com/WebBreacher/WhatsMyName)
  (CC BY-SA 4.0), ~230 from [Sherlock](https://github.com/sherlock-project/sherlock) (MIT) and ~280 from
  [Maigret](https://github.com/soxoj/maigret) (MIT), including 92 Italian forums and sites
- **extended list** (~9,300 forums and minor sites, Maigret): turned on with the "Extended list" switch or
  `--extended`; searches become much longer

**Avatars**: at the end of a search UserSnex downloads the public profile pictures (one request each,
7-day cache in `.usersnex_cache/avatars`), shows them in the list, in the details and in the graph nodes,
and compares their visual fingerprint. Empty images, and images shared by 4+ accounts with no other
links between them (default avatars), do not count. It can be turned off in Config > Options.

Only `usersnex.yaml` is in the repository: the other lists are downloaded from their own repositories on
first launch and afterwards from the **Config** tab ("Update lists" button) or with
`python run.py --update-lists`. They are converted and kept in `lists/`, in separate files with their own
licence. `usersnex.yaml` is updated from there too, from the repository
https://github.com/tingon11/UserSnex (the address can be changed in Config). If a list cannot be
downloaded or looks broken, the previous one is kept. Adult sites and sites that require a POST request
are excluded; those behind Cloudflare / captcha start disabled.

**Self-test** (`python run.py --selftest` or the "Site self-test" button): for each site it tries a real
account and a made-up one. Sites that give wrong answers or errors from your network are recorded in
`site_health.json` and skipped; the Sites tab shows the status of each one.

## Site format

```yaml
name: GitHub
category: sviluppo         # category key (Italian; translated in the interface)
url: https://github.com/{username}            # profile page for people
check_url: https://api.github.com/users/{username}
type: json                 # json | html
regex: "^[A-Za-z0-9-]{1,39}$"   # valid usernames (optional)
require: login             # JSON path that must exist (optional)
missing_status: [404]      # codes meaning "does not exist" (default 404, 410)
absent_text: ""            # text (or list) meaning "does not exist" (optional)
present_text: ""           # text (or list) that must be present (optional)
found_status: [200]        # only these codes mean "exists" (optional)
known: ["torvalds"]        # existing account, for the self-test
rate_per_sec: 1            # maximum requests per second to the host
fields:                    # profile field -> path in the JSON (e.g. them.0.profile.bio)
  display_name: name
  bio: bio
  links:
    - blog
    - {path: twitter_username, template: "https://x.com/{}"}
    - {path: proofs, key: service_url}     # list of objects
```

## Rules

Public data and public APIs only, no login. UserSnex identifies itself with its own User-Agent, respects
the sites' limits and **does not bypass protections**: a 401/403/429 or an anti-bot check page becomes a
visible error ("check by hand"), not a result. This is why Instagram, X, Facebook and TikTok are not
included: they require a login or block programs.

## Languages and terms of use

Interface in italiano, English, Deutsch, Français, Español, Nederlands, Polski, Ελληνικά and Shqip. On
first launch you choose the language and accept the terms of use (`TERMS.md`, generated by
`usersnex/terms.py`); the language can then be changed from the **Language** menu in the sidebar.
Translations are in `usersnex/i18n_strings.py`.

## Licences

UserSnex's code is licensed under the **GNU GPL v3 or later** (`LICENSE`) with an additional
**attribution requirement** (GPL section 7(b), `ADDITIONAL_TERMS.md`): anyone redistributing it, even
modified or inside another program, must keep visible, in the About & licences window and in the
documentation, the notice

> Basato su UserSnex di Andrea Cumini - Osintinfo.net - andrea@osintinfo.net

("Based on UserSnex by Andrea Cumini - Osintinfo.net - andrea@osintinfo.net").

Third-party site lists stay in their own files, each with its own licence and attribution (summary in
`NOTICE`, full texts in `THIRD_PARTY_NOTICES.md`):

| File | Source | Licence |
|---|---|---|
| `usersnex/sites/usersnex.yaml` | UserSnex | GPL v3 or later |
| `lists/whatsmyname.yaml` (downloaded) | WhatsMyName, © Micah Hoffman | CC BY-SA 4.0 (the derived file stays CC BY-SA 4.0) |
| `lists/sherlock.yaml` (downloaded) | Sherlock Project | MIT |
| `lists/maigret.yaml` (downloaded) | Maigret, © Soxoj | MIT |
