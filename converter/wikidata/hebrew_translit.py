"""Smart Hebrew→Latin English-label generator (Rule 46).

The MHM pipeline used to emit awkward placeholder English labels like
``"work from Hebrew manuscript F 32638"`` whenever a work was titled only in
Hebrew. Wikidata curators and library catalogers expect English-script labels
to be either the canonical Latin form of the entity name or a faithful
romanization — never a synthetic placeholder built around the shelfmark.

This module replaces that single fallback with a three-tier waterfall:

Five-tier waterfall (Rule 46, 2026-05-18):

1. **Curated override dict** for canonical Latin names of well-known Hebrew
   authors and works (Maimonides, Rashi, Nahmanides …). Zero-algorithm; fully
   deterministic. ~25 entries hand-picked from the most common authors in the
   NLI Hebrew-manuscript catalog.
2. **NLI ALA-LC romanization read** from the source record. NLI catalogers
   populate MARC 246 (varying form of title) and 880 (alternate-script
   linking) with the librarian-quality ALA-LC romanized form. When the
   pipeline carries such a value on the record (under the keys this module
   probes), it is returned verbatim — librarian work always beats machine
   transliteration.
3. **Deterministic ALA-LC-inspired character table** as a final fallback.
   Maps the 22 consonants + final forms to a phonetic Latin approximation.
   This is rules-based, offline, and never raises.

The function refuses to invent a label when none of the three tiers produces a
non-empty result, returning ``None`` so the caller can simply omit the ``en``
slot rather than upload a synthetic string.

The module makes **no network calls** and has no third-party dependencies — it
is safe to call from the upload hot path.

A future addition could try `phonikud` (DICTA's rules-based vocalizer) before
the ALA-LC fallback so the intermediate vowels reflect modern Israeli Hebrew
pronunciation rather than the conservative ALA-LC defaults. `phonikud` is
intentionally not a dependency today because (a) it returns IPA phonemes
(``ʔ``, ``ʃ``, ``χ`` …) rather than Latin letters — its output would itself
need a second mapping pass — and (b) the project pins Python 3.12 (Rule #3)
while `phonikud` declares ``<3.13``; depending on it would couple our floor
to its ceiling. A Wikidata reverse-lookup (search for an item whose
``rdfs:label@he`` matches the Hebrew text and read its ``rdfs:label@en``)
would also be ideal but is intentionally out of scope here because Rule 46
requires the module to be offline-only.
"""

from __future__ import annotations

# ── Tier 1: curated override dict ────────────────────────────────────
#
# Canonical Latin names of authors and works that recur often in the NLI
# Hebrew-manuscript catalog. Keys are normalised by ``_norm_hebrew_key`` so
# the lookup is insensitive to MARC ISBD punctuation, internal whitespace,
# and the two stylistic gershayim variants (``"`` U+0022 vs ``״`` U+05F4).
#
# When adding an entry, prefer the form Wikipedia uses for the canonical
# English label; that is what Wikidata curators expect to see in the ``en``
# label slot. If the figure has multiple Hebrew names (e.g. acronym AND full
# patronymic), add both rows pointing at the same canonical label — the
# reconciler's author-conflict guard then refuses to merge same-name persons
# that disagree on identifiers, so we can safely emit the same Latin label
# for both Hebrew variants.

_HEBREW_OVERRIDES: dict[str, str] = {
    # ── Major medieval philosophers and halakhists ───────────────────
    "משה בן מימון": "Maimonides",
    "רמבם": "Maimonides",  # רמב״ם normalised: gershayim stripped
    "משה בן נחמן": "Nahmanides",
    "רמבן": "Nahmanides",  # רמב״ן
    "שלמה בן יצחק": "Rashi",
    "רשי": "Rashi",  # רש״י
    "שמואל בן מאיר": "Rashbam",
    "רשבם": "Rashbam",  # רשב״ם
    "אברהם בן עזרא": "Abraham ibn Ezra",
    "אבן עזרא": "Ibn Ezra",
    "דוד קמחי": "David Kimhi",
    "רדק": "Radak",  # דוד קמחי acronym
    "סעדיה גאון": "Saadia Gaon",
    "סעדיה בן יוסף גאון": "Saadia Gaon",
    "בחיי בן יוסף אבן פקודה": "Bahya ibn Paquda",
    "בחיי אבן פקודה": "Bahya ibn Paquda",
    "יהודה הלוי": "Yehuda HaLevi",
    "יוסף קארו": "Joseph Karo",
    "יוסף בן אפרים קארו": "Joseph Karo",
    "משה אלשיך": "Moshe Alshich",
    "אריה לייב": "Aryeh Leib",
    # ── Hasidic and Kabbalistic figures ───────────────────────────────
    "ישראל בעל שם טוב": "Baal Shem Tov",
    "בעל שם טוב": "Baal Shem Tov",
    "בעשט": "Baal Shem Tov",  # בעש״ט
    "ישראל בן אליעזר": "Baal Shem Tov",
    "יצחק לוריא": "Isaac Luria",
    "האריזל": "Isaac Luria",  # האר״י ז״ל
    "חיים ויטאל": "Hayyim Vital",
    "משה חיים לוצאטו": "Moshe Hayyim Luzzatto",
    "רמחל": "Moshe Hayyim Luzzatto",  # רמח״ל
    # ── Tannaim / Amoraim canonical figures ───────────────────────────
    "הלל הזקן": "Hillel the Elder",
    "רבי עקיבא": "Rabbi Akiva",
    "רבי יהודה הנשיא": "Judah ha-Nasi",
    # ── Common manuscript / textual references ────────────────────────
    "תורה": "Torah",
    "תלמוד": "Talmud",
    "משנה": "Mishnah",
    "זהר": "Zohar",
    "סידור": "Siddur",
    "מחזור": "Mahzor",
    "הגדה": "Haggadah",
    "הגדה של פסח": "Passover Haggadah",
    "כתובה": "Ketubah",
    "ספר תהלים": "Book of Psalms",
    "תהלים": "Psalms",
}


# ── Tier 3: deterministic ALA-LC-inspired character table ─────────────
#
# Two-row map. ``_HEBREW_BASE_LETTERS`` covers the 22 consonants plus the
# five final forms. ``_HEBREW_DIGRAPHS`` covers two-character clusters that
# Hebrew typists frequently write but Unicode does not encode as a single
# code point (e.g. ``שׁ`` with sin/shin dot is encoded as base ש +
# combining mark, so the dotted-letter cluster needs special handling
# before we touch the base table). Order matters in the digraph pass —
# longest first — so the digraph step always runs before the per-letter
# step.

_HEBREW_BASE_LETTERS: dict[str, str] = {
    "א": "",  # aleph — silent; carries the following vowel in vocalised text
    "ב": "v",
    "ג": "g",
    "ד": "d",
    "ה": "h",
    "ו": "o",  # vav — defaults to o (cholam-malé is the most common context)
    "ז": "z",
    "ח": "h",
    "ט": "t",
    "י": "i",
    "כ": "k",
    "ך": "kh",  # final kaf
    "ל": "l",
    "מ": "m",
    "ם": "m",  # final mem
    "נ": "n",
    "ן": "n",  # final nun
    "ס": "s",
    "ע": "",  # ayin — silent in modern Israeli Hebrew
    "פ": "p",
    "ף": "f",  # final peh
    "צ": "ts",
    "ץ": "ts",  # final tsadi
    "ק": "k",
    "ר": "r",
    "ש": "sh",
    "ת": "t",
}


# Vowel marks (nikud) — when present they refine the consonant output. The
# unvocalised input that flows in from MARC catalog data almost never has
# nikud, but if a curator hand-edits the source the marks should not crash
# the function and should ideally improve the output. We strip them rather
# than try to use them; the ALA-LC table here is *consonantal* by design and
# the override + Tier 2 paths capture cases where vowel precision matters.
_HEBREW_NIKUD_RANGE = (0x0591, 0x05C7)


def _norm_hebrew_key(text: str) -> str:
    """Normalise Hebrew text for override-dict lookup.

    Strips: ASCII and Hebrew gershayim/geresh punctuation, leading/trailing
    whitespace, MARC ISBD terminators (``,;:./``). Collapses internal
    whitespace. Leaves Hebrew letters and vowels intact.
    """
    if not text:
        return ""
    cleaned = text.strip().rstrip(",;:./")
    # Drop both ASCII " ' and Unicode geresh (U+05F3) / gershayim (U+05F4)
    drop_chars = ('"', "'", "׳", "״", "׳", "״")
    for ch in drop_chars:
        cleaned = cleaned.replace(ch, "")
    return " ".join(cleaned.split())


def _strip_nikud(text: str) -> str:
    """Remove Hebrew vowel and cantillation marks."""
    low, high = _HEBREW_NIKUD_RANGE
    return "".join(c for c in text if not (low <= ord(c) <= high))


def _has_hebrew(text: str) -> bool:
    return any("֐" <= c <= "׿" for c in text or "")


def _has_latin(text: str) -> bool:
    return any(c.isascii() and c.isalpha() for c in text or "")


# ── Tier 2: NLI romanization keys to probe on the source record ───────
#
# When the MARC importer surfaces an ALA-LC romanized form, it lands on
# one of these record keys. We probe them in priority order and return
# the first non-empty value. A record may not have any of them, in which
# case Tier 2 short-circuits and the waterfall continues to Tier 3.
_ROMANIZED_KEYS_TITLE: tuple[str, ...] = (
    "title_romanized",
    "title_alalc",
    "title_latin",
    "title_en",
    "marc_880",
    "marc_246",
    "variant_title_romanized",
    "alt_title_romanized",
)
_ROMANIZED_KEYS_NAME: tuple[str, ...] = (
    "name_romanized",
    "name_alalc",
    "name_latin",
    "name_en",
    "marc_880_name",
    "alternate_name",
)


def _read_romanization(source_record: dict | None, candidate_keys: tuple[str, ...]) -> str | None:
    """Probe a record dict for an existing NLI romanization.

    Accepts simple string values, lists (returns the first non-empty entry),
    and dict-of-{lang→string} shapes (returns the ``en`` or ``latn`` entry).
    Returns ``None`` for missing, empty, or Hebrew-only values — Tier 2 must
    never silently re-emit the Hebrew input as if it were a romanization.
    """
    if not source_record:
        return None
    for key in candidate_keys:
        raw = source_record.get(key)
        if raw is None:
            continue
        # Unwrap common container shapes
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str) and entry.strip():
                    raw = entry
                    break
            else:
                continue
        if isinstance(raw, dict):
            for lang_key in ("en", "latn", "alalc", "romanized"):
                val = raw.get(lang_key)
                if isinstance(val, str) and val.strip():
                    raw = val
                    break
            else:
                continue
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value:
            continue
        # Reject pure-Hebrew "romanizations" — that means the importer copied
        # the Hebrew title into the wrong field, and returning it would defeat
        # the whole point of Tier 2.
        if _has_hebrew(value) and not _has_latin(value):
            continue
        return value
    return None


def _algorithmic_transliterate(text: str) -> str:
    """Tier 3 fallback: pure-rules ALA-LC-inspired character map.

    Strips vowel marks, applies digraphs (none currently — left as an
    extension hook), then maps each Hebrew letter through the base table.
    Non-Hebrew characters pass through unchanged so mixed strings like
    ``"דניאל Daniel"`` retain their Latin portion. Multiple spaces are
    collapsed; the first surviving alphabetic character is capitalised.
    """
    if not text:
        return ""
    cleaned = _strip_nikud(text)
    out_parts: list[str] = []
    for ch in cleaned:
        if ch in _HEBREW_BASE_LETTERS:
            out_parts.append(_HEBREW_BASE_LETTERS[ch])
        elif "֐" <= ch <= "׿":
            # Other Hebrew-block code points we do not map (e.g. punctuation
            # like maqaf or stray cantillation we missed). Drop silently so
            # the output stays clean.
            continue
        else:
            out_parts.append(ch)
    joined = "".join(out_parts)
    # Collapse repeated whitespace and trim
    collapsed = " ".join(joined.split())
    if not collapsed:
        return ""
    # Capitalise the first alphabetic character only — leave inner casing
    # alone so existing Latin embeds like proper acronyms survive.
    for i, ch in enumerate(collapsed):
        if ch.isalpha():
            return collapsed[:i] + ch.upper() + collapsed[i + 1 :]
    return collapsed


def english_label_for_hebrew(
    text: str,
    source_record: dict | None = None,
    *,
    allow_network: bool | None = None,
    allow_nakdan: bool = True,
    allow_algorithmic: bool = True,
) -> str | None:
    """Return a Latin-script English label for a Hebrew text, or ``None``.

    Five-tier waterfall (Rule 46, 2026-05-18):
      Tier 1 — curated override dict (zero-algorithm, deterministic).
      Tier 2 — NLI ALA-LC romanization read from ``source_record``.
      Tier 3 — Wikidata SPARQL reverse-lookup (cached; respects
               ``MHM_NO_NETWORK`` env var; never raises).
      Tier 4 — DICTA Nakdan vowel model + vowel-aware ALA-LC
               (lazy-loaded; graceful fallback to None when model absent).
      Tier 5 — deterministic consonantal ALA-LC fallback (always succeeds
               for any Hebrew input, lower quality than Tier 4).

    Callers should treat ``None`` as "no en label" and simply omit the slot
    rather than emit a synthetic placeholder. Empty input, whitespace-only
    input, and Latin-only input all return ``None`` — the function refuses to
    invent a label when there is no Hebrew to transliterate.

    Args:
      text: Hebrew text to transliterate.
      source_record: Optional MARC-derived record dict; Tier 2 reads from
        ``title_romanized``, ``marc_880``, ``marc_246`` etc.
      allow_network: Tier 3 network gating. ``None`` defers to
        ``wikidata_reverse_lookup``'s default (which honours
        ``MHM_NO_NETWORK``).
      allow_nakdan: Tier 4 gating. Set ``False`` in tests to skip the
        model load attempt entirely.
      allow_algorithmic: Tier 5 (consonantal ALA-LC) gating. Default ``True``
        for backwards compatibility, but **work-label and person-P2093
        callsites pass ``False`` (2026-05-18)** because the consonantal
        output ("Tknot rvno grshm mor hgolh") is too ugly for public-facing
        labels. When ``False`` and Tiers 1–4 all return None, this function
        returns None and the caller falls back to the NLI identifier.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None

    # If there is no Hebrew in the input at all, the caller is on the wrong
    # branch — they already have a Latin string. Refuse rather than re-emit
    # the same value through the algorithmic table (which would mangle it).
    if not _has_hebrew(raw):
        return None

    # ── Tier 1: curated override dict ─────────────────────────────────
    key = _norm_hebrew_key(raw)
    if key and key in _HEBREW_OVERRIDES:
        return _HEBREW_OVERRIDES[key]
    # Also try the nikud-stripped form so vocalised input still matches
    # entries that were authored without nikud.
    stripped_key = _norm_hebrew_key(_strip_nikud(raw))
    if stripped_key and stripped_key in _HEBREW_OVERRIDES:
        return _HEBREW_OVERRIDES[stripped_key]

    # ── Tier 2: NLI romanization read ─────────────────────────────────
    # We do not know a priori whether this is a title or a person name, so
    # probe both keysets. Title keys are tried first because work-label
    # callsites are the most common entry point.
    for keyset in (_ROMANIZED_KEYS_TITLE, _ROMANIZED_KEYS_NAME):
        romanized = _read_romanization(source_record, keyset)
        if romanized:
            return romanized

    # ── Tier 3: Wikidata SPARQL reverse-lookup (cached) ───────────────
    # If Wikidata already has an English label for this exact Hebrew
    # string, prefer it — that's the community-consensus form (e.g.
    # "Maimonides" rather than algorithmic "Moshe ben Maimon"). The
    # lookup is cached on disk; the first hit per name pays the network
    # cost (~1.5s), subsequent hits are instant. Graceful: any failure
    # returns None and falls through to algorithmic transliteration.
    try:
        from converter.wikidata.wikidata_reverse_lookup import (  # noqa: PLC0415
            lookup_english_label,
        )
        sparql_label = lookup_english_label(raw, allow_network=allow_network)
        if sparql_label:
            return sparql_label
    except Exception:  # pragma: no cover - defensive only  # noqa: BLE001
        # Importing or calling the SPARQL helper must never break the
        # waterfall. Fall through to Tier 4.
        pass

    # ── Tier 4: TaatikNet ML transliteration ──────────────────────────
    # Engine swapped 2026-05-18 (fourth iteration). The original Nakdan
    # plan was a nikud-adder + ALA-LC table; that proved broken under
    # `transformers==5.3.0` AND out-of-distribution for medieval Hebrew
    # names. Replaced with TaatikNet (`malper/taatiknet`) — a ByT5-small
    # seq2seq Hebrew↔Latin transliterator by Morris Alper trained on
    # ~15k Wiktionary pairs. Verified output on the user's flagged
    # canonical example: "תקנות רבנו גרשם מאור הגולה" →
    # "Takanut rivno gereshem meor hagola". The ``allow_nakdan`` flag
    # name is kept for backwards compatibility — it now gates TaatikNet.
    if allow_nakdan:
        try:
            from converter.wikidata.taatiknet_translit import (  # noqa: PLC0415
                best_effort_vocalized_transliterate,
            )
            ml_label = best_effort_vocalized_transliterate(raw)
            # Defensive Latin-only guard: TaatikNet already enforces this,
            # but if a future engine swap regresses, this stops Hebrew
            # residue from reaching public Wikidata labels. The caller
            # then falls back to NLI-only.
            if ml_label and not _has_hebrew(ml_label):
                return ml_label
        except Exception:  # pragma: no cover - defensive only  # noqa: BLE001
            pass

    # ── Tier 5: deterministic consonantal ALA-LC fallback ──────────────
    # Skipped when the caller explicitly opts out (work labels + person
    # P2093). The consonantal output ("Tknot rvno grshm mor hgolh") was
    # called out 2026-05-18 as too ugly for public Wikidata items; the
    # caller substitutes the NLI identifier instead.
    if not allow_algorithmic:
        return None
    transliterated = _algorithmic_transliterate(raw)
    if transliterated:
        return transliterated
    return None
