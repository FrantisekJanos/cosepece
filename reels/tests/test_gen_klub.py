"""Smlouva serveru a generátoru (F4): údaje o zadaných PN ze serveru až do zadání a karty.

Vstup je společný soubor `tests/contract/focus-pn-info.json`, který čte i PHP test výstupu `ReelJobs::pnInfo`.
Model, render i ffprobe jsou podvržené (fixture `fake` z test_gen_generate.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from test_gen_generate import _run, fake  # noqa: F401

CONTRACT = Path(__file__).resolve().parents[2] / "tests" / "contract" / "focus-pn-info.json"


def test_pn_info_ze_serveru_az_do_karty(tmp_path, fake):  # noqa: F811
    info = json.loads(CONTRACT.read_text(encoding="utf-8"))
    r = _run(tmp_path, focus_pn=[9801, 9802], focus_info=info, render=False)
    # PN 9802 nemá text (has_text: false) → jen formát pn
    assert r.formats == ["pn"]
    out = tmp_path / "out"
    zadani = (out / "podklady" / "zadani.md").read_text(encoding="utf-8")
    assert "Test Poslanec (klub ANO ke dni podání 15. 3. 2026)" in zadani
    # normalizace zapíše autory ze serveru do karty: klub ke dni podání, člen vlády bez klubu
    card = json.loads((out / "variant-2" / "card.json").read_text(encoding="utf-8"))
    assert card["pn_autor"] == [{"cislo": 9801, "jmeno": "Test Poslanec", "klub": "ANO"},
                                {"cislo": 9802, "jmeno": "Test Ministr", "klub": None}]
