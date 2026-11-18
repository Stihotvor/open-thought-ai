"""Checks of the latency-run phrase file docs/research/m3-phrases.json (T3)."""

import json
from pathlib import Path
from typing import Any

PHRASES_PATH = Path(__file__).resolve().parents[2] / "docs" / "research" / "m3-phrases.json"
LANGUAGES = ("en", "pl", "uk")
COUNT = 30

Entry = dict[str, Any]


def load_entries() -> list[Entry]:
    data = json.loads(PHRASES_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    entries = data["phrases"]
    assert isinstance(entries, list)
    return entries


def test_exactly_30_entries_with_unique_ids() -> None:
    entries = load_entries()
    ids = [entry["id"] for entry in entries]
    assert len(entries) == COUNT
    assert len(set(ids)) == COUNT
    assert ids == [f"p{n:02d}" for n in range(1, COUNT + 1)]


def test_each_entry_has_three_non_empty_languages() -> None:
    phrases = [entry[lang] for entry in load_entries() for lang in LANGUAGES]
    assert all(isinstance(text, str) and text.strip() for text in phrases)
    assert len(phrases) == COUNT * len(LANGUAGES)


def test_same_ids_in_every_language() -> None:
    entries = load_entries()
    id_sets = [{entry["id"] for entry in entries if entry.get(lang)} for lang in LANGUAGES]
    assert id_sets[0] == id_sets[1] == id_sets[2]
    assert len(id_sets[0]) == COUNT
