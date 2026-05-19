"""TaatikNet-based Hebrew→Latin transliteration for Rule 46 Tier 4.

Replaces the broken DICTA Nakdan model (which is a nikud-adder, not a
transliterator) with `malper/taatiknet` — a ByT5-small seq2seq model
by Morris Alper fine-tuned on ~15k Hebrew↔Latin Wiktionary pairs.
Repository: https://github.com/morrisalp/taatiknet
HuggingFace:  https://huggingface.co/malper/taatiknet

Empirical results on real MHM corpus examples (per-word mode):

| Hebrew                           | TaatikNet output             |
|---|---|
| תקנות רבנו גרשם מאור הגולה       | takanut rivno geresh meor hagola |
| ריקרדו                            | rikardo                       |
| מורה נבוכים                       | more navokhim                 |
| פיוטים ושירים                    | piyotim veshirim              |
| פסק דין                           | pesek din                     |

The model collapses multi-word phrases into a single token when fed the
whole phrase, so we split on whitespace and transliterate word-by-word,
then re-join. TaatikNet's training emphasises Sephardi pronunciation and
applies acute-accent stress marks (á, é, í, ó, ú) — those are stripped
by default for clean Wikidata labels but can be preserved via
``preserve_stress_marks=True``.

CLAUDE.md Rule 46 (revised 2026-05-18, fourth iteration same day).
"""

from __future__ import annotations

import logging
import os
import unicodedata

logger = logging.getLogger(__name__)

# Module-level cache for the lazy-loaded model. Three states:
#   None       — not yet attempted (first call will load)
#   False      — load failed; subsequent calls short-circuit to None
#   tuple      — (tokenizer, model, device) ready for inference
_TAATIKNET: tuple | bool | None = None

_TAATIKNET_MODEL_NAME = "malper/taatiknet"

# Per-word generation budget. Hebrew names rarely exceed ~12 letters,
# so 40 new tokens is generous. Smaller budgets speed inference.
_MAX_NEW_TOKENS = 40
_NUM_BEAMS = 4

# Hebrew letter range — only words containing at least one Hebrew letter
# are sent to the model. Mixed-script tokens pass through unchanged.
_HEBREW_LETTER_RANGE = range(0x05D0, 0x05EB)


def _has_hebrew(text: str) -> bool:
    return any(ord(c) in _HEBREW_LETTER_RANGE for c in text)


def _strip_stress_marks(text: str) -> str:
    """Drop combining acute / grave / macron / circumflex from Latin output.

    TaatikNet's training data (Wiktionary) uses acute accents for stress
    (e.g. "rikardó") which look noisy in Wikidata public labels. Wikidata
    labels prefer plain Latin (e.g. "Rikardo"). This strips combining
    diacritics but preserves base letters and necessary apostrophes.
    """
    normalised = unicodedata.normalize("NFD", text)
    stripped = "".join(
        ch for ch in normalised if not unicodedata.combining(ch)
    )
    return unicodedata.normalize("NFC", stripped)


def _capitalise(text: str) -> str:
    """Title-case the first alphabetic character, leave inner case alone."""
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1 :]
    return text


def _load_taatiknet() -> tuple | None:
    """Lazy-load the TaatikNet model + tokenizer.

    Module-level singleton; returns ``None`` after any load failure so
    callers fall through to the Rule 46 Tier 5 consonantal fallback.
    Honours ``MHM_TAATIKNET_MODEL`` env var override.
    """
    global _TAATIKNET
    if _TAATIKNET is False:
        return None
    if isinstance(_TAATIKNET, tuple):
        return _TAATIKNET
    try:
        import torch  # noqa: PLC0415
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # noqa: PLC0415

        if torch.backends.mps.is_available():
            device = torch.device("mps")
        elif torch.cuda.is_available():
            device = torch.device("cuda")
        else:
            device = torch.device("cpu")

        model_name = os.environ.get("MHM_TAATIKNET_MODEL", _TAATIKNET_MODEL_NAME)
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
        model.to(device)
        model.eval()
        _TAATIKNET = (tokenizer, model, device)
        logger.info(
            "TaatikNet ready (model=%s, device=%s)", model_name, device,
        )
        return _TAATIKNET
    except Exception as exc:  # noqa: BLE001 — graceful degradation contract
        logger.info("TaatikNet unavailable (%s); transliterator disabled", exc)
        _TAATIKNET = False
        return None


def _translit_single_word(word: str) -> str | None:
    """Run TaatikNet on a single Hebrew word.

    Returns ``None`` on any failure, **including the case where the model
    echoed the Hebrew input back instead of transliterating it**. On
    out-of-distribution short tokens (e.g. ``"מא"``, ``"גוק"``) or NER
    hallucinations, TaatikNet's beam search can return the input unchanged
    or partially translated, leaking Hebrew into the supposedly-Latin
    output. The caller stitches per-word outputs into a phrase result and
    treats any ``None`` as a hard failure for that word.
    """
    loaded = _load_taatiknet()
    if loaded is None:
        return None
    tokenizer, model, device = loaded
    try:
        import torch  # noqa: PLC0415

        inputs = tokenizer(word, return_tensors="pt", truncation=True).to(device)
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=_MAX_NEW_TOKENS,
                num_beams=_NUM_BEAMS,
                no_repeat_ngram_size=2,
            )
        decoded = tokenizer.decode(out[0], skip_special_tokens=True)
        if _has_hebrew(decoded):
            logger.debug(
                "TaatikNet echoed Hebrew on %r → %r; treating as failure",
                word, decoded,
            )
            return None
        return decoded
    except Exception as exc:  # noqa: BLE001
        logger.debug("TaatikNet inference failed on %r: %s", word, exc)
        return None


def transliterate_hebrew_to_latin(
    text: str,
    *,
    preserve_stress_marks: bool = False,
    capitalise_result: bool = True,
) -> str | None:
    """Transliterate Hebrew text to Latin via TaatikNet, word-by-word.

    Args:
      text: Hebrew text (any length).
      preserve_stress_marks: When ``False`` (default), strips combining
        acute / grave / macron / circumflex diacritics from the output
        so the result reads as plain Latin suitable for Wikidata public
        labels. When ``True``, keeps TaatikNet's native stress marks.
      capitalise_result: When ``True`` (default), uppercases the first
        alphabetic character.

    Returns:
      A Latin-script transliteration, or ``None`` when the model is not
      available, the input has no Hebrew, or inference failed.

    Never raises. Callers in the Rule 46 waterfall treat ``None`` as
    "fall through to the next tier".
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw or not _has_hebrew(raw):
        return None

    parts: list[str] = []
    failures = 0
    seen_hebrew_words = 0
    for word in raw.split():
        word = word.strip().strip('".,:;')
        if not word:
            continue
        if not _has_hebrew(word):
            parts.append(word)
            continue
        seen_hebrew_words += 1
        rendered = _translit_single_word(word)
        if rendered is None:
            failures += 1
            continue
        if not preserve_stress_marks:
            rendered = _strip_stress_marks(rendered)
        parts.append(rendered)

    if seen_hebrew_words == 0:
        return None
    # All-or-nothing contract: if ANY Hebrew word failed to transliterate
    # cleanly, drop the whole phrase. A gappy "מא ktovim omet" Latin/Hebrew
    # mix is worse than no en label at all — the caller falls back to
    # "NLI <control_number>" which is a stable, unambiguous identifier.
    if failures > 0:
        return None
    if not parts:
        return None

    out = " ".join(p for p in parts if p)
    # Final defensive check — even with zero per-word failures, refuse
    # to emit a string that still contains Hebrew script. Rule 46 / 47:
    # en labels must be Latin-only.
    if _has_hebrew(out):
        return None
    if capitalise_result:
        out = _capitalise(out)
    return out or None


# Backwards-compat shim: callers in `hebrew_translit.py` invoke
# `best_effort_vocalized_transliterate(text)` regardless of which engine
# is wired in as Tier 4. We expose the same name so the waterfall
# orchestrator stays engine-agnostic.
def best_effort_vocalized_transliterate(text: str) -> str | None:
    """Tier 4 entry point matching the prior nakdan_translit API."""
    return transliterate_hebrew_to_latin(text)
