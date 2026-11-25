import pytest

pytest.importorskip("customtkinter")
from usersnex.gui.graphview import name_groups, norm_name  # noqa: E402
from usersnex.gui.widgets import sort_key  # noqa: E402


def test_sort_key_numbers_text_and_empty():
    vals = ["99%", "9%", "35%", "—", ""]
    assert sorted(vals, key=sort_key) == ["9%", "35%", "99%", "—", ""]
    assert sorted(["#2", "#10", "#1"], key=sort_key) == ["#1", "#2", "#10"]
    assert sorted(["Zeta", "ábaco", "Beta"], key=sort_key) == ["ábaco", "Beta", "Zeta"]


def test_names_are_merged_across_sites():
    names = {"GitHub": "Luna Verdi", "DEV": "luna  verdi", "Mastodon": "Luna 🌿", "Codeberg": "Luna V.",
             "Chess": "Luis Verdi", "X": "lunaverdi92"}
    g = name_groups(names, "lunaverdi92")
    assert norm_name("Lúna  VERDI!") == "luna verdi"
    assert g["luna verdi"][1] == ["DEV", "GitHub"]             # co-occorrenza: un solo nodo
    assert "luna" in g and "luna v" in g and "luis verdi" in g
    assert all("lunaverdi92" not in k.replace(" ", "") for k in g)   # uguale all'username: saltato
