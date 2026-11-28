"""Kontrakt šablon webu, které čte widget chatu (rag/hlidac_rag/chat/static/kontext.mjs).

Šablony patří Honzovi; když se značky změní, widget ztratí kontext obrazovky. Test čte soubory jako text."""
from pathlib import Path

KOREN = Path(__file__).resolve().parents[2]


def test_tisk_php_ma_znacky_ktere_cte_widget():
    t = (KOREN / "tisk.php").read_text(encoding="utf-8")
    assert '<li class="pn-item" id="pn-<?= (int)$p[\'cislo\'] ?>">' in t, "widget chatu čte li.pn-item#pn-N (chat/static/kontext.mjs)"
    assert '<li class="vote">' in t and "hlasy.sqw?g=<?= (int)$v['id_hlasovani'] ?>" in t


def test_pripravovane_ma_kotvu():
    assert 'id="prep-<?= e((string)$u[\'pid\']) ?>"' in (KOREN / "pripravovane.php").read_text(encoding="utf-8")


def test_cesty_odpovidaji_url_php():
    u = (KOREN / "app" / "Url.php").read_text(encoding="utf-8")
    for cesta in ["'tisky' => 'tisky'", "'zakon' => 'zakony'", "'hledat' => 'hledat'", "'data' => 'data-a-api'", "'revize' => 'revize'"]:
        assert cesta in u
