"""Fixed confirm texts and the yes match (D1). Pure: no I/O, no state, no LLM.

Language keys match ``LANGUAGE_NAMES`` in ``agent.py``.
"""

import string

DONE: dict[str, str] = {"en": "Done.", "pl": "Gotowe.", "uk": "Готово."}
FAILED: dict[str, str] = {
    "en": "That did not work.",
    "pl": "To się nie udało.",
    "uk": "Це не вдалося.",
}
CANCELLED: dict[str, str] = {"en": "Cancelled.", "pl": "Anulowano.", "uk": "Скасовано."}
QUESTION: dict[str, str] = {
    "en": "Should I run {what}? Say yes to confirm.",
    "pl": "Czy mam uruchomić {what}? Powiedz tak, aby potwierdzić.",
    "uk": "Запустити {what}? Скажіть так, щоб підтвердити.",
}
YES_WORDS: dict[str, frozenset[str]] = {
    "en": frozenset({"yes", "yeah"}),
    "pl": frozenset({"tak"}),
    "uk": frozenset({"так"}),
}
_STRIP = string.whitespace + string.punctuation + "…«»„“”‘’¡¿–—"


def question(language: str, source: str, tool: str) -> str:
    """The fixed question; ``tool`` is the bare name (the caller splits on ``SEP``)."""
    return QUESTION[language].format(what=f"{source} {tool}")


def done(language: str) -> str:
    return DONE[language]


def failed(language: str) -> str:
    return FAILED[language]


def cancelled(language: str) -> str:
    return CANCELLED[language]


def is_yes(text: str, language: str) -> bool:
    """True only if the whole text, lowercased and end-stripped, is a yes word of ``language``."""
    words = YES_WORDS.get(language, frozenset())
    return text.lower().strip(_STRIP) in words
