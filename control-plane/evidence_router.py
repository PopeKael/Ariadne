"""Deterministic evidence-first routing for Home responses.

The language model may interpret a request, but it does not get to decide that
cheaply verifiable factual claims can be answered from memory.  This module is
deliberately small and provider-independent: it returns policy facts only;
execution remains in the existing Home/Vault path and the search registry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


VERIFY_WORDS = frozenset({"check", "verify", "research", "look", "lookup", "find"})
CURRENT_WORDS = frozenset({
    "current", "currently", "now", "today", "still", "latest", "recent", "recently",
    "status", "price", "prices", "version", "versions", "release", "released", "update",
    "updated", "available", "exists", "working", "works", "open", "closed",
})
PRECISION_WORDS = frozenset({"exact", "exactly", "date", "dates", "when", "quote", "quoted", "quotation", "statistic", "statistics", "percentage", "number"})
CONFLICT_WORDS = frozenset({"conflicting", "contradictory", "contradiction", "disagree", "but", "versus", "vs"})
FACTUAL_STARTERS = frozenset({"who", "what", "where", "when", "which", "is", "are", "was", "were", "did", "does", "do", "how"})
GENERIC_NAMED_WORDS = frozenset({
    "what", "where", "when", "which", "who", "is", "are", "was", "were", "did", "does", "do",
    "how", "the", "this", "that", "still", "there", "and", "or", "it", "work", "works",
})


@dataclass(frozen=True)
class EvidenceDecision:
    """The controller's evidence requirements for one Home request."""

    use_vault: bool
    external_search: bool
    verification_required: bool
    current_information: bool
    explicit_verification: bool
    named_or_obscure: bool
    reason_codes: tuple[str, ...]
    failure_message: str = "I couldn't verify that from the Vault or live sources, so I don't want to guess."

    def as_dict(self) -> dict[str, Any]:
        return {
            "use_vault": self.use_vault,
            "external_search": self.external_search,
            "verification_required": self.verification_required,
            "current_information": self.current_information,
            "explicit_verification": self.explicit_verification,
            "named_or_obscure": self.named_or_obscure,
            "reason_codes": list(self.reason_codes),
            "failure_message": self.failure_message,
        }


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").casefold())


def _capitalized_name_phrase(query: str) -> bool:
    # This intentionally errs toward verification for named places/products.
    # A title-cased two-word phrase is cheap to verify and expensive to invent.
    return bool(re.search(r"\b[A-Z][A-Za-z0-9'’-]+(?:\s+[A-Z][A-Za-z0-9'’-]+){1,}\b", query))


def classify_request(query: str, planner_result: dict[str, Any] | None = None) -> dict[str, Any]:
    words = _words(query)
    word_set = set(words)
    planner = planner_result if isinstance(planner_result, dict) else {}
    semantic = planner.get("semantic") if isinstance(planner.get("semantic"), dict) else {}
    reasons: list[str] = []

    explicit = bool(word_set.intersection(VERIFY_WORDS)) or bool(re.search(r"\blook\s+up\b|\bfind\s+out\b", query.casefold()))
    conversational_greeting = bool(re.search(r"\bhow\s+(are|is)\s+you\b|\bhow'?s\s+it\s+going\b", query.casefold()))
    current = (bool(word_set.intersection(CURRENT_WORDS)) or bool(re.search(r"\b20\d{2}\b|\bthis\s+(month|week|year)\b", query.casefold()))) and not conversational_greeting
    precise = bool(word_set.intersection(PRECISION_WORDS))
    conflict = bool(word_set.intersection(CONFLICT_WORDS))
    named = _capitalized_name_phrase(query)
    if not named:
        meaningful = [word for word in words if len(word) >= 4 and word not in GENERIC_NAMED_WORDS]
        named = len(meaningful) >= 2 and bool(word_set.intersection({"place", "town", "city", "person", "product", "company", "organisation", "organization", "clock", "museum", "station", "building"}))
    planner_current = bool(semantic.get("needs_current_information"))
    planner_low_confidence = isinstance(semantic.get("confidence"), (int, float)) and float(semantic["confidence"]) < 0.55
    planner_ambiguous = str(semantic.get("ambiguity") or "").casefold() == "high"
    factual = bool(words and words[0] in FACTUAL_STARTERS) and not conversational_greeting

    for enabled, code in (
        (current or planner_current, "current_information"),
        (explicit, "explicit_verification"),
        (named, "named_or_obscure"),
        (precise, "precision_claim"),
        (conflict or planner_ambiguous, "conflicting_or_ambiguous"),
        (planner_low_confidence, "low_interpretation_confidence"),
    ):
        if enabled:
            reasons.append(code)

    verification_required = bool(reasons) and (factual or explicit or named or current or planner_current or precise or conflict or planner_low_confidence or planner_ambiguous)
    return {
        "current_information": current or planner_current,
        "explicit_verification": explicit,
        "named_or_obscure": named,
        "precision_claim": precise,
        "conflicting_or_ambiguous": conflict or planner_ambiguous,
        "low_interpretation_confidence": planner_low_confidence,
        "factual": factual,
        "verification_required": verification_required,
        "reason_codes": reasons,
    }


def decide(
    query: str,
    *,
    planner_result: dict[str, Any] | None,
    vault_mode: str,
    vault_available: bool,
    search_available: bool,
    attachments_present: bool = False,
) -> EvidenceDecision:
    classification = classify_request(query, planner_result)
    semantic = planner_result.get("semantic") if isinstance(planner_result, dict) and isinstance(planner_result.get("semantic"), dict) else {}
    personal = bool(semantic.get("needs_personal_history"))
    personal = personal or bool(re.search(r"\b(my|our|we|wazza|warren|chanya|ariadne|vault|prior|remember|discussed)\b", query.casefold()))
    mode = vault_mode if vault_mode in {"auto", "always", "never"} else "auto"
    if mode == "always":
        use_vault = vault_available
    elif mode == "never":
        use_vault = False
    else:
        # Verification requests get a Vault pass before going outside.  This
        # is intentionally broader than personal history: a local note may be
        # the cheapest and most authoritative answer available.
        use_vault = vault_available and (personal or classification["verification_required"] or bool(attachments_present and semantic.get("needs_attachment")))

    external = bool(classification["verification_required"] and search_available and mode != "never")
    return EvidenceDecision(
        use_vault=use_vault,
        external_search=external,
        verification_required=bool(classification["verification_required"]),
        current_information=bool(classification["current_information"]),
        explicit_verification=bool(classification["explicit_verification"]),
        named_or_obscure=bool(classification["named_or_obscure"]),
        reason_codes=tuple(classification["reason_codes"]),
    )


def external_search_needed(
    decision: EvidenceDecision,
    *,
    vault_source_count: int = 0,
    attachment_source_count: int = 0,
) -> bool:
    """Apply evidence precedence after local retrieval has actually run."""
    if not decision.external_search:
        return False
    if attachment_source_count > 0 and not decision.current_information and not decision.explicit_verification:
        return False
    if vault_source_count > 0 and not decision.current_information and not decision.explicit_verification:
        return False
    return True


__all__ = ["EvidenceDecision", "classify_request", "decide", "external_search_needed"]
