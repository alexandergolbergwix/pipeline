"""Shared entity-text normalisation for the NER inference pipelines.

The BIO decoder in ``inference_pipeline.py`` (persons) and
``ner_inference_pipeline.py`` (provenance + contents) emits entity
spans by joining whitespace-split tokens. Tokens often include trailing
punctuation that's part of the source text but not part of the entity
name itself — e.g., ``"מ' גסטר."`` (a name followed by a sentence-end
period) or ``"[=1826],"`` (a bracketed Gregorian-equivalent date).

This helper trims terminal punctuation, opening/closing brackets, and
ASCII double quotes from the edges. It deliberately preserves
*internal* Hebrew gershayim ``"`` (used in Hebrew abbreviations like
``רמב"ם``) and geresh ``'`` (used as an abbreviation marker, e.g.
``מ' גסטר``). Internal whitespace is collapsed.

Used by:

- ``ner.inference_pipeline.JointNERPipeline.extract_entities``
- ``ner.ner_inference_pipeline.NERInferencePipeline.process_text``
"""
from __future__ import annotations

import re

# Whitespace + leading wrapper characters: brackets, ASCII double quote,
# and zero-width / non-breaking spaces. The MARC equivalence markers
# ``=`` (Gregorian equivalent) and ``~`` (approximate) are also stripped
# because they appear in patterns like ``[=1826]`` / ``[~1500]`` that
# wrap a year inside cataloguing metadata — never inside a person name.
# ASCII apostrophe and Hebrew geresh are NOT in this class — they're
# part of names like ``י' תשבי`` even when at the start of a span.
_LEADING_GARBAGE_RE = re.compile(
    r"^[\s\u00a0\u200b\(\[\{\"=~]+"
)

# Trailing whitespace, terminal punctuation, closing brackets, and
# ASCII double quote. ASCII apostrophe is NOT stripped at the trailing
# edge — would damage rare names that legitimately end in geresh.
_TRAILING_GARBAGE_RE = re.compile(
    r"[\s\u00a0\u200b\u2026\.,;:!?\)\]\}\"]+$"
)

_INTERNAL_WS_RE = re.compile(r"\s+")


def normalize_entity_text(text: str) -> str:
    """Strip leading/trailing punctuation + collapse internal whitespace.

    Returns ``""`` if the input is empty after stripping.
    """
    if not text:
        return ""
    cleaned = _LEADING_GARBAGE_RE.sub("", text)
    cleaned = _TRAILING_GARBAGE_RE.sub("", cleaned)
    cleaned = _INTERNAL_WS_RE.sub(" ", cleaned).strip()
    return cleaned
