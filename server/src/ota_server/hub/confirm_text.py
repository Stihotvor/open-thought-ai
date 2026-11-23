"""Fixed confirm texts and the yes match (D1). Pure: no I/O, no state, no LLM.

Language keys match ``LANGUAGE_NAMES`` in ``agent.py``.
"""

import re
import string
from collections.abc import Mapping

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
WITH: dict[str, str] = {"en": "with", "pl": "z", "uk": "з"}
SOME_TEXT: dict[str, str] = {"en": "some text", "pl": "jakiś tekst", "uk": "якийсь текст"}
MORE: dict[str, str] = {"en": "and more", "pl": "i więcej", "uk": "та інше"}
MAX_ARGS = 3  # args spoken in the question; the rest become MORE
_SAFE_WORD = re.compile(r"[A-Za-z0-9_.\-]{1,24}")  # a key or a short token value
YES_WORDS: dict[str, frozenset[str]] = {
    "en": frozenset({"yes", "yeah"}),
    "pl": frozenset({"tak"}),
    "uk": frozenset({"так"}),
}
_STRIP = string.whitespace + string.punctuation + "…«»„“”‘’¡¿–—"


def _pick(texts: dict[str, str], language: str) -> str:
    """The text for ``language``; English for an unknown language, so it never raises."""
    return texts.get(language, texts["en"])


def _value(value: object, language: str) -> str:
    """A bounded spoken value. Free text is never spoken verbatim."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float) and len(str(value)) <= 24:
        return str(value)
    if isinstance(value, str) and _SAFE_WORD.fullmatch(value):
        return value
    return _pick(SOME_TEXT, language)


def args_summary(args: Mapping[str, object], language: str) -> str:
    """A short, safe summary of validated args, for example ``level 30``.

    At most ``MAX_ARGS`` args. A key or a string value that is not a short token,
    and any list or object, is spoken as a fixed placeholder, never verbatim.
    """
    parts = [
        f"{key if _SAFE_WORD.fullmatch(key) else '?'} {_value(value, language)}"
        for key, value in list(args.items())[:MAX_ARGS]
    ]
    if len(args) > MAX_ARGS:
        parts.append(_pick(MORE, language))
    return ", ".join(parts)


def question(
    language: str, source: str, tool: str, args: Mapping[str, object] | None = None
) -> str:
    """The fixed question; ``tool`` is the bare name (the caller splits on ``SEP``).

    ``args`` are the validated args of the held call, spoken as a safe summary (D1).
    """
    what = f"{source} {tool}"
    summary = args_summary(args or {}, language)
    if summary:
        what = f"{what} {_pick(WITH, language)} {summary}"
    return _pick(QUESTION, language).format(what=what)


def done(language: str) -> str:
    return _pick(DONE, language)


def failed(language: str) -> str:
    return _pick(FAILED, language)


def cancelled(language: str) -> str:
    return _pick(CANCELLED, language)


def is_yes(text: str, language: str) -> bool:
    """True only if the whole text, lowercased and end-stripped, is a yes word of ``language``."""
    words = YES_WORDS.get(language, frozenset())
    return text.lower().strip(_STRIP) in words
