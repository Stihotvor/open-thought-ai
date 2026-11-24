"""Moderation results: the known category schemas and one verdict per result."""

from typing import Any

# Moderation (docs/security.md). Every category is blocked except these: a child may ask
# about a headache. An unknown category that is true also blocks.
ALLOWED_CATEGORIES = frozenset({"health", "financial", "law", "pii"})
# A result must carry one complete known schema, so a stub or a partial answer fails closed.
MISTRAL_CORE = frozenset(
    {
        "sexual",
        "hate_and_discrimination",
        "violence_and_threats",
        "dangerous_and_criminal_content",
        "selfharm",
    }
)
# Mistral since 2026: "dangerous_and_criminal_content" split in two.
MISTRAL_SPLIT_CORE = (MISTRAL_CORE - {"dangerous_and_criminal_content"}) | {"dangerous", "criminal"}
OPENAI_CORE = frozenset({"sexual", "hate", "harassment", "self-harm", "violence"})
KNOWN_CORES = (MISTRAL_CORE, MISTRAL_SPLIT_CORE, OPENAI_CORE)


def flagged(result: dict[str, Any]) -> bool:
    """One moderation result: flagged when "flagged" is true or any category outside
    ALLOWED_CATEGORIES is true. An unknown shape raises ValueError, so the turn fails closed."""
    categories = result.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("moderation result without categories")
    if not any(categories.keys() >= core for core in KNOWN_CORES):
        raise ValueError("unknown moderation schema")
    if not all(isinstance(value, bool) for value in categories.values()):
        raise ValueError("moderation categories must be booleans")
    flagged = result.get("flagged", False)
    if not isinstance(flagged, bool):
        raise ValueError("moderation flag must be a boolean")
    return flagged or any(
        value for name, value in categories.items() if name not in ALLOWED_CATEGORIES
    )
