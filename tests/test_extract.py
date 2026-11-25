from usersnex.extract import get_path, profile_from_html, profile_from_json


def test_get_path_dicts_and_lists():
    data = {"them": [{"basics": {"username": "mario"}}]}
    assert get_path(data, "them.0.basics.username") == "mario"
    assert get_path(data, "them.1.basics") is None
    assert get_path(data, "nope.x") is None


def test_profile_from_json_fields_links_and_bio_urls():
    data = {"name": "Mario Rossi", "bio": "dev <b>python</b> https://mario.dev", "blog": "mario.dev",
            "tw": "mrossi", "proofs": [{"service_url": "https://github.com/mario"}]}
    fields = {"display_name": "name", "bio": "bio",
              "links": ["blog", {"path": "tw", "template": "https://x.com/{}"},
                        {"path": "proofs", "key": "service_url"}]}
    p = profile_from_json(data, fields)
    assert p.display_name == "Mario Rossi"
    assert "<b>" not in p.bio
    assert "https://x.com/mrossi" in p.links
    assert "https://github.com/mario" in p.links
    assert "https://mario.dev" in p.links


def test_profile_from_html_meta():
    html = ('<html><head><title>T</title><meta property="og:title" content="Mario">'
            '<meta property="og:description" content="ciao https://mario.dev">'
            '<meta property="og:image" content="https://img/x.png"></head></html>')
    p = profile_from_html(html)
    assert p.display_name == "Mario"
    assert p.avatar == "https://img/x.png"
    assert p.links == ["https://mario.dev"]


def test_numeric_created_at_becomes_date():
    p = profile_from_json({"c": 1500000000, "ms": 1500000000000}, {"created_at": "c"})
    assert p.created_at == "2017-07-14"
    assert profile_from_json({"ms": 1500000000000}, {"created_at": "ms"}).created_at == "2017-07-14"
