"""Testy konců popisků pro FB a IG."""
from __future__ import annotations

import pytest

from hlidac_reels import caption


def test_caption_fb_s_ct():
    assert caption.for_network("Text", 221, "fb") == (
        "Text\n\nCelý rozbor: https://cosepece.cz/tisk.php?ct=221"
    )


def test_caption_ig():
    assert caption.for_network("Text", 221, "ig") == "Text\n\nOdkaz na celý rozbor v profilu."


def test_caption_bez_ct():
    assert caption.for_network("Text", None, "fb") == "Text\n\nhttps://cosepece.cz"
    assert caption.for_network("Text", None, "ig") == "Text\n\nOdkaz na celý rozbor v profilu."


def test_caption_neznama_sit():
    with pytest.raises(ValueError):
        caption.for_network("Text", 1, "x")  # type: ignore[arg-type]
