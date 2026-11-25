from usersnex import banlist, mytargets
from usersnex.sites import _parse, to_entry


def site(name="Forum", url="https://f.example/u/{username}", **kw):
    return _parse({"name": name, "url": url, "check_url": url, "type": "html", **kw}, "test")


def test_ban_stays_if_same_definition_and_is_released_if_changed(tmp_path):
    path = str(tmp_path / "banlist.json")
    banlist.ban([site()], path=path)
    same = [site()]
    n, released = banlist.apply(same, path=path)
    assert n == 1 and released == [] and same[0].banned and not same[0].enabled
    changed = [site(url="https://f.example/members/{username}")]       # la lista aggiornata l'ha sistemato
    n, released = banlist.apply(changed, path=path)
    assert n == 0 and released == ["Forum"] and not changed[0].banned and changed[0].enabled
    assert banlist.load(path) == {}                                    # il ban è decaduto


def test_unban_and_untouched_sites(tmp_path):
    path = str(tmp_path / "banlist.json")
    banlist.ban([site("A"), site("B")], path=path)
    assert banlist.unban(["a"], path=path) == 1
    sites = [site("A"), site("B"), site("C")]
    n, _ = banlist.apply(sites, path=path)
    assert n == 1 and [s.banned for s in sites] == [False, True, False]


def test_edit_of_a_list_site_keeps_hidden_rules():
    base = to_entry(site("Maigret Forum", absent_text=["Not found"], regex="^[a-z]+$",
                         fields={"display_name": "name"}, known=["alice"], source="Maigret"))
    new = mytargets.build("Maigret Forum", "forum", "https://f.example/new/{username}", "", "status", "", "")
    d = mytargets.merge(base, new, keep_check=True)
    assert d["url"] == "https://f.example/new/{username}"
    assert d["absent_text"] == ["Not found"] and "found_status" not in d       # verifica originale
    assert d["regex"] == "^[a-z]+$" and d["fields"] == {"display_name": "name"} and d["known"] == ["alice"]
    assert d["source"] == "mytargets (Maigret)"
    changed = mytargets.merge(base, mytargets.build("Maigret Forum", "forum", base["url"], "", "present",
                                                    "Member since", ""), keep_check=False)
    assert changed["present_text"] == ["Member since"] and "absent_text" not in changed
