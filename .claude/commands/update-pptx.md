Update the Bar-Ilan MHM PowerPoint deck and the Hebrew speaker notes together.

## Use this when

- The slide content changes
- The Hebrew delivery notes change
- The talk framing changes
- The deck needs regeneration after any research or system update

## Golden Rule — Re-Extract Before Every Edit

**ALWAYS read the current slide text and speaker notes straight from the saved
PPTX at the start of the turn, before changing a single word — even for a
one-sentence fix.** Never edit, quote, or rewrite from memory, from earlier in
the chat, from `slide_specs()`, or from the generated transcript. The PPTX on
disk may have been hand-edited in PowerPoint since you last saw it, so any
remembered text is potentially stale. The pattern is always: re-extract the
current notes/text → edit the PPTX in place → rebuild with
`build_pptx_deck.py`.

## Two Paths — Modify In Place Is The Default

- **Light path (DEFAULT) — `bar_ilan_deck_v2.py` or `edit_pptx_deck.py --deck v2`.**
  For all text and speaker-note edits, including one-sentence fixes and Hebrew
  RTL/lang/font normalization (`--fix-notes`). Edits the existing PPTX in place,
  backs up first, saves the same file, and refreshes the transcript — no redraw.
- **Heavy path — `build_pptx_deck_v2.py --rebuild` (or `bar_ilan_deck_v2.py --rebuild`).**
  ONLY when the visual design, layout, slide count/order, or design-function
  defaults change. Rebuilds the whole deck from Python layout functions.

Shared infrastructure lives in `docs/presentations/pptx_toolkit.py`.

## Workflow (light path — text/notes edits)

1. Re-extract the current text first (Golden Rule), straight from the saved PPTX:
   ```bash
   .venv/bin/python docs/presentations/bar_ilan_deck_v2.py --show 2
   ```
2. Edit in place — speaker notes by default; `--where text` for slide copy,
   `--where any` for both:
   ```bash
   .venv/bin/python docs/presentations/bar_ilan_deck_v2.py \
       --slide 2 --where notes \
       --replace "old sentence" "new sentence"
   ```
   Fix Hebrew RTL / language / font on all notes without changing wording:
   ```bash
   .venv/bin/python docs/presentations/bar_ilan_deck_v2.py --fix-notes
   ```
   Add a new RTL speaker-note paragraph with `--append-note "..."`. The script
   saves the PPTX (with a timestamped backup under `_backups/`) and refreshes
   the transcript automatically.
3. Verify: re-show the slide, confirm each slide still has notes, the transcript
   matches the deck, and every untouched slide is unchanged.

## Workflow (heavy path — design/structure changes only)

1. Re-extract current notes/text first (same Golden Rule).
2. Edit `slide_specs()` / design functions in
   `docs/presentations/build_pptx_deck.py`.
3. Rebuild — the script reads the current PPTX first, then redraws:
   ```bash
   .venv/bin/python docs/presentations/build_pptx_deck.py --rebuild
   ```
4. Verify: 8 slides, each has notes, no duplicate/overlapping visible text, the
   transcript matches, and manual PowerPoint edits were preserved.

After either path, if the talk story or claims changed, sync `AGENTS.md`,
`CLAUDE.md`, and the relevant LaTeX files before finishing.

## Rule Of Thumb

- Slides: English, clean, concise, conference-facing.
- Speaker notes: Hebrew, spoken delivery, transitions and detail.
- PPTX: editable living draft and source of truth for the next AI/LLM pass.
- Generated transcript: useful for audio, never the source of truth.
- Current notes: whatever is embedded in the saved PPTX right now.
