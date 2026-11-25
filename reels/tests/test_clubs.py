"""Popisky poslaneckých klubů: Python, web (Clubs::label() v PHP) a záloha šablony v3 musí mít stejné páry."""
from __future__ import annotations

import re
from pathlib import Path

CLUBS_PHP = Path(__file__).resolve().parents[2] / "app" / "Clubs.php"
REEL_JS = Path(__file__).resolve().parents[1] / "hlidac_reels" / "template_v3" / "reel.js"


def _php_labels() -> dict[str, str]:
    # větve match v Clubs::label(): 'X' => 'Y' i 'A', 'B' => 'Y'; default se nebere
    src = CLUBS_PHP.read_text(encoding="utf-8")
    body = re.search(r"function label\(.*?match\s*\(\$club\)\s*\{(.*?)\};", src, re.S)
    assert body, "Clubs::label() bez match"
    pairs: dict[str, str] = {}
    for keys, value in re.findall(r"^\s*((?:'[^']*'\s*,\s*)*'[^']*')\s*=>\s*'([^']*)'", body.group(1), re.M):
        for key in re.findall(r"'([^']*)'", keys):
            pairs[key] = value
    assert pairs, "v match žádné páry"
    return pairs


def _js_labels() -> dict[str, str]:
    # var KLUBY = { ANO2011: 'ANO', 'Nezařaz': 'nezařazení', … } v template_v3/reel.js
    src = REEL_JS.read_text(encoding="utf-8")
    body = re.search(r"var KLUBY\s*=\s*\{(.*?)\};", src, re.S)
    assert body, "v reel.js chybí var KLUBY"
    pairs = {quoted or bare: value
             for quoted, bare, value in re.findall(r"(?:'([^']*)'|([A-Za-z0-9_]+))\s*:\s*'([^']*)'", body.group(1))}
    assert pairs, "KLUBY bez párů"
    return pairs


def test_popisky_jako_web():
    from hlidac_reels.clubs import LABELS
    assert LABELS == _php_labels()


def test_zaloha_sablony_v3_stejna():
    from hlidac_reels.clubs import LABELS
    assert _js_labels() == LABELS


def test_label():
    from hlidac_reels.clubs import label
    assert label(None) == ""
    assert label("ODS") == "ODS"
    assert label("TOP09") == "TOP 09"
    assert label("ANO2011") == "ANO"
    assert label("Nezařaz") == "nezařazení"
