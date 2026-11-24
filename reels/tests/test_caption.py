"""Testy konců popisků pro FB a IG."""
from __future__ import annotations

import pytest

from hlidac_reels import caption


def test_caption_fb_s_ct():
    assert caption.for_network("Text", 221, "fb") == (
        "Text\n\nCelý rozbor: https://cosepece.cz/tisk/221"
    )


def test_caption_ig():
    assert caption.for_network("Text", 221, "ig") == "Text\n\nOdkaz na celý rozbor v profilu."


def test_caption_bez_ct():
    assert caption.for_network("Text", None, "fb") == "Text\n\nhttps://cosepece.cz"
    assert caption.for_network("Text", None, "ig") == "Text\n\nOdkaz na celý rozbor v profilu."


def test_caption_neznama_sit():
    with pytest.raises(ValueError):
        caption.for_network("Text", 1, "x")  # type: ignore[arg-type]


# --- Threads (≤ 500 znaků: první odstavec do 440 znaků + odkaz) -------------

def test_threads_text_prvni_odstavec_a_odkaz():
    text = "První odstavec. Druhá věta.\n\nDruhý odstavec se nepoužije.\n\n#hashtag"
    assert caption.for_network(text, 221, "threads") == (
        "První odstavec. Druhá věta.\n\nCelý rozbor: https://cosepece.cz/tisk/221"
    )
    # prázdný řádek s mezerami také odděluje odstavce, okraje se ořežou
    assert caption.for_network("  Úvod.  \n  \nDál", 5, "threads") == (
        "Úvod.\n\nCelý rozbor: https://cosepece.cz/tisk/5"
    )
    # FB a IG beze změny
    assert caption.for_network(text, 221, "fb") == text + "\n\nCelý rozbor: https://cosepece.cz/tisk/221"
    assert caption.for_network(text, 221, "ig") == text + "\n\nOdkaz na celý rozbor v profilu."


def test_threads_text_dlouha_veta_do_500():
    ending = "\n\nCelý rozbor: https://cosepece.cz/tisk/12345"
    # přesně 440 znaků zůstane celé
    exact = "a" * 439 + "."
    assert caption.for_network(exact, 12345, "threads") == exact + ending
    # delší odstavec se zkrátí na poslední celou větu do 440 znaků
    s1 = "Krátká věta. " + "b" * 300 + "? " + "c" * 50 + "! "
    para = s1 + "d" * 200 + ". Konec."
    out = caption.for_network(para, 12345, "threads")
    assert out == s1.rstrip() + ending
    assert len(out) <= 500
    # konec věty „…“
    para2 = "e" * 400 + "… " + "f" * 100
    assert caption.for_network(para2, 12345, "threads") == "e" * 400 + "…" + ending
    # „…“ na 441. znaku už se do limitu nevejde
    para2b = "e" * 200 + " " + "e" * 239 + "…" + "f" * 100
    assert caption.for_network(para2b, 12345, "threads") == "e" * 200 + "…" + ending
    # konec věty až za hranicí 440 se nepoužije (bez mezery: tvrdé zkrácení)
    para3 = "g" * 445 + ". " + "h" * 10
    assert caption.for_network(para3, 12345, "threads") == "g" * 439 + "…" + ending
    # bez konce věty: poslední mezera do 439 znaků + „…“
    words = ("slovo " * 120).strip()
    out4 = caption.for_network(words, 12345, "threads")
    first = out4[: -len(ending)]
    assert first.endswith("slovo…") and len(first) <= 440 and "  " not in first
    assert len(out4) <= 500
    # bez mezery i bez konce věty: tvrdé zkrácení na 439 + „…“
    out5 = caption.for_network("x" * 1000, 12345, "threads")
    assert out5 == "x" * 439 + "…" + ending
    assert len(out5) <= 500


def test_threads_text_bez_ct():
    assert caption.for_network("Text.\n\nDál", None, "threads") == "Text.\n\nhttps://cosepece.cz"
