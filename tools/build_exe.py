#!/usr/bin/env python3
# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Crea UserSnex.exe con PyInstaller, in un file solo (onefile), con i dati accanto in dist/UserSnex/.

Da eseguire su Windows, dalla cartella del progetto (o da qualunque cartella: i percorsi sono assoluti):

    python tools\\build_exe.py              # cartella dist\\UserSnex\\ con UserSnex.exe e i dati (+ zip)
    python tools\\build_exe.py --console    # exe con la finestra della console (per vedere gli errori)
    python tools\\build_exe.py --zip-only   # rifa solo lo zip da dist\\UserSnex\\ gia' compilata

Serve PyInstaller nello stesso ambiente Python della GUI:  pip install pyinstaller

Niente UPX, informazioni di versione Windows e icona incorporate, moduli inutili esclusi.

Dentro l'exe: solo il codice, le librerie, i dati di customtkinter e gli assets (logo, icona). I dati
restano FUORI, accanto all'exe in dist/UserSnex/: sites/usersnex.yaml (i siti curati), docs/manual
(manuali e screenshot), LICENSE, NOTICE, THIRD_PARTY_NOTICES.md, ADDITIONAL_TERMS.md, TERMS.md.
I file di lavoro vengono scaricati o creati accanto all'exe (o in %LOCALAPPDATA%\\UserSnex se quella non
e' scrivibile) e non sono mai nella release: le liste WhatsMyName / Sherlock / Maigret (lists/),
mytargets.yaml, config.json, banlist.json, site_health.json, results/ e la cache.

Risultato:  dist/UserSnex/UserSnex.exe  (+ zip dist/UserSnex-<versione>-windows-x64.zip con tutta la cartella e il suo .sha256)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(REPO, "dist")
BUILD = os.path.join(REPO, "build")
ASSETS = os.path.join(REPO, "usersnex", "gui", "assets")

PRIVATE = ("config.json", "banlist.json", "site_health.json", "mytargets.yaml", "lists", "results", ".usersnex_cache")
DOCS = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "ADDITIONAL_TERMS.md", "TERMS.md")
EXCLUDES = ["pydoc", "doctest", "test", "tests", "lib2to3", "xmlrpc", "pdb", "cProfile", "profile",
            "tkinter.test", "distutils", "setuptools", "pip", "numpy", "matplotlib", "scipy", "pandas", "pytest"]


def say(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def version() -> str:
    sys.path.insert(0, REPO)
    try:
        from usersnex import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return "dev"


def version_tuple(v: str) -> tuple[int, int, int, int]:
    nums = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    return tuple((nums + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def write_version_info(path: str, ver: str) -> None:
    """File di versione Windows per PyInstaller (un exe senza queste informazioni insospettisce gli antivirus)."""
    t = version_tuple(ver)
    txt = f"""# -*- coding: utf-8 -*-
VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Osintinfo.net'),
      StringStruct('FileDescription', 'UserSnex - username OSINT search with identity correlation'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'UserSnex'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 Andrea Cumini - GPL-3.0-or-later'),
      StringStruct('OriginalFilename', 'UserSnex.exe'),
      StringStruct('ProductName', 'UserSnex'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)


def ensure_icon() -> str:
    ico = os.path.join(ASSETS, "usersnex.ico")
    if not os.path.isfile(ico):                      # icona dal logo, se manca
        from PIL import Image
        src = next(p for p in (os.path.join(ASSETS, n) for n in ("usersnex_512.png", "usersnex.png", "logo.png"))
                   if os.path.isfile(p))
        Image.open(src).convert("RGBA").save(ico, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
        say(f"icona creata: {ico}")
    return ico


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_exe(console: bool) -> str:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller non installato:  pip install pyinstaller")
    sep = ";" if os.name == "nt" else ":"
    ver = version()
    os.makedirs(BUILD, exist_ok=True)
    vinfo = os.path.join(BUILD, "version_info.txt")
    write_version_info(vinfo, ver)
    for name in DOCS:
        p = os.path.join(REPO, name)
        if not os.path.isfile(p):
            sys.exit(f"manca {name}: deve stare nella release")
    datas = [(ASSETS, os.path.join("usersnex", "gui", "assets"))]   # nel bundle solo logo e icona
    folder = os.path.join(DIST, "UserSnex")
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onefile", "--name", "UserSnex",
           "--noupx",                                   # UPX e' il primo motivo di falsi positivi
           "--icon", ensure_icon(),
           "--distpath", folder, "--workpath", BUILD, "--specpath", BUILD,
           "--collect-all", "customtkinter",            # customtkinter ha file .json/.otf che altrimenti mancano
           "--hidden-import", "PIL.ImageTk", "--hidden-import", "PIL._tkinter_finder",
           "--hidden-import", "tkinter.ttk", "--hidden-import", "tkinter.filedialog"]
    if os.name == "nt":
        cmd += ["--version-file", vinfo]
    for m in EXCLUDES:
        cmd += ["--exclude-module", m]
    if not console:
        cmd.append("--windowed")
    for src, dst in datas:
        cmd += ["--add-data", f"{src}{sep}{dst}"]
    cmd.append(os.path.join(REPO, "gui.py"))
    say(" ".join(cmd))
    subprocess.run(cmd, cwd=REPO, check=True)
    copy_data(folder)
    exe = os.path.join(folder, "UserSnex" + (".exe" if os.name == "nt" else ""))
    if not os.path.isfile(exe):
        sys.exit("PyInstaller non ha prodotto l'eseguibile (vedi i messaggi sopra)")
    for name in PRIVATE:                             # mai dati personali o liste scaricate nella release
        if os.path.exists(os.path.join(folder, name)):
            sys.exit(f"dist/UserSnex/{name} non deve stare nella release: toglilo e rilancia")
    archive, digest = make_zip(ver)
    say(f"fatto: {folder}  ·  zip: {os.path.basename(archive)}  ·  sha256: {digest}")
    return exe


def copy_data(folder: str) -> None:
    """I dati accanto all'exe, fuori dal bundle (vedi usersnex/paths.py: APP_DIR, data_path)."""
    os.makedirs(os.path.join(folder, "sites"), exist_ok=True)
    shutil.copy2(os.path.join(REPO, "usersnex", "sites", "usersnex.yaml"), os.path.join(folder, "sites"))
    shutil.copytree(os.path.join(REPO, "docs", "manual"), os.path.join(folder, "docs", "manual"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "Thumbs.db", "desktop.ini"))
    for name in DOCS:
        shutil.copy2(os.path.join(REPO, name), folder)


def make_zip(ver: str) -> tuple[str, str]:
    """Zip della release da dist/UserSnex/ (cartella radice UserSnex/), con lo schema degli altri progetti:
    dist/UserSnex-<versione>-windows-x64.zip e il suo .sha256."""
    archive = shutil.make_archive(os.path.join(DIST, f"UserSnex-{ver}-windows-x64"), "zip",
                                  root_dir=DIST, base_dir="UserSnex")
    digest = sha256(archive)
    with open(archive + ".sha256", "w", encoding="ascii", newline="\n") as f:
        f.write(f"{digest}  {os.path.basename(archive)}\n")
    return archive, digest


def main() -> int:
    ap = argparse.ArgumentParser(description="UserSnex: eseguibile Windows in un file (PyInstaller) con i dati accanto, e zip della release")
    ap.add_argument("--console", action="store_true", help="exe con console (utile per vedere gli errori)")
    ap.add_argument("--zip-only", action="store_true", help="rifa solo lo zip da dist/UserSnex/ gia' compilata")
    args = ap.parse_args()
    if args.zip_only:
        if not os.path.isfile(os.path.join(DIST, "UserSnex", "UserSnex.exe")):
            sys.exit("manca dist/UserSnex/UserSnex.exe: compila prima")
        archive, digest = make_zip(version())
        say(f"zip: {os.path.basename(archive)}  ·  sha256: {digest}")
        return 0
    build_exe(args.console)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
