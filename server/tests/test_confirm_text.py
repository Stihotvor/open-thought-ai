"""confirm_text: fixed confirm texts and the exact-match yes rule (M2, M3, T6)."""

import pytest

from ota_server.hub import confirm_text
from ota_server.hub.confirm_text import cancelled, done, failed, is_yes, question

LANGUAGES = ("en", "pl", "uk")


@pytest.mark.parametrize(
    ("language", "text"),
    [("en", "Yes."), ("en", "yeah "), ("pl", "Tak!"), ("uk", "Так.")],
)
def test_a_yes_word_is_a_yes(language: str, text: str) -> None:
    assert is_yes(text, language) is True


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "no"),
        ("en", "yes but later"),
        ("en", ""),
        ("en", "tak"),
        ("pl", "yes"),
        ("uk", "tak"),
    ],
)
def test_a_non_yes_is_not_a_yes(language: str, text: str) -> None:
    assert is_yes(text, language) is False


def test_an_unknown_language_is_not_a_yes() -> None:
    assert is_yes("yes", "de") is False


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_text_exists_for_every_language(language: str) -> None:
    texts = [question(language, "device", "set_volume"), done(language)]
    texts += [failed(language), cancelled(language)]
    for text in texts:
        assert isinstance(text, str)
        assert text.strip()


def test_the_english_texts_are_fixed() -> None:
    assert (done("en"), failed("en"), cancelled("en")) == (
        "Done.",
        "That did not work.",
        "Cancelled.",
    )
    assert confirm_text.DONE["en"] == "Done."


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_question_names_what_runs(language: str) -> None:
    text = question(language, "device", "set_volume")
    assert "device" in text
    assert "set_volume" in text


def test_an_unknown_language_falls_back_to_english() -> None:
    assert question("xx", "device", "set_volume") == question("en", "device", "set_volume")
    assert (done("xx"), failed("xx"), cancelled("xx")) == (
        "Done.",
        "That did not work.",
        "Cancelled.",
    )
