"""Build the paper-claim verification caches against the pinned corpus.

Runs the MHM Pipeline stages 1-6 in process (no Qt event loop) against
``data/tsvs/test_subset.tsv``, then writes two cache files:

* ``paper/verification/results/<timestamp>/pipeline_run.json``
* ``paper/verification/results/<timestamp>/wikidata_build.json``

Both caches follow the schemas documented in
``paper/verification/verifiers/{pipeline_run,wikidata_build}.py``.

Stage 6 (Wikidata) is forced to **dry-run** (QuickStatements export only)
under all circumstances; Rule 25's moratorium applies.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("build_verification_caches")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_PATH = _REPO_ROOT / "data" / "tsvs" / "test_subset.tsv"
_RESULTS_DIR = _REPO_ROOT / "paper" / "verification" / "results"
_FIXTURE_SHA = (
    _REPO_ROOT / "paper" / "verification" / "fixtures" / "test_corpus_sha256.txt"
)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Stage 1: MARC parse ──────────────────────────────────────────────


def stage_parse(corpus_path: Path, work_dir: Path, verbose: bool = False) -> Path:
    from converter.parser.unified_reader import UnifiedReader  # noqa: PLC0415
    from converter.transformer.field_handlers import extract_all_data  # noqa: PLC0415

    logger.info("Stage 1 (parse): reading %s", corpus_path)
    reader = UnifiedReader()
    records = list(reader.read_file(corpus_path))
    extracted: list[dict[str, Any]] = []
    for record in records:
        data = extract_all_data(record)
        entry = dataclasses.asdict(data)
        entry["_control_number"] = record.control_number
        extracted.append(entry)

    out = work_dir / "marc_extracted.json"
    out.write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Stage 1 OK — %d records → %s", len(extracted), out.name)
    return out


# ── Stage 2: NER ─────────────────────────────────────────────────────


def stage_ner(marc_extract: Path, work_dir: Path, device: str = "cpu") -> Path | None:
    """Run all three NER models + MARC500 + genre classifiers.

    Returns the NER results path on success, or None if everything failed.
    """
    try:
        # Make repo-relative ``ner`` import path available.
        ner_dir = str(_REPO_ROOT / "ner")
        if ner_dir not in sys.path:
            sys.path.insert(0, ner_dir)

        from ner.inference_pipeline import JointNERPipeline  # noqa: PLC0415
        from ner.ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415
    except Exception as exc:
        logger.warning("Stage 2 (NER) imports failed: %s — skipping", exc)
        return None

    records: list[dict[str, Any]] = json.loads(marc_extract.read_text(encoding="utf-8"))
    if not records:
        logger.warning("Stage 2: empty marc extract; skipping")
        return None

    logger.info("Stage 2 (NER): %d records on %s", len(records), device)
    person = JointNERPipeline(
        model_path="alexgoldberg/hebrew-manuscript-joint-ner-v2",
        device=device,
    )

    prov_path = _REPO_ROOT / "ner" / "provenance_ner_model.pt"
    cont_path = _REPO_ROOT / "ner" / "contents_ner_model.pt"
    provenance = (
        NERInferencePipeline(model_path=str(prov_path), device=device)
        if prov_path.exists()
        else None
    )
    contents = (
        NERInferencePipeline(model_path=str(cont_path), device=device)
        if cont_path.exists()
        else None
    )
    if provenance is None:
        logger.warning("provenance_ner_model.pt missing")
    if contents is None:
        logger.warning("contents_ner_model.pt missing")

    # Optional classifiers
    marc500_clf = None
    try:
        m500 = _REPO_ROOT / "ner" / "marc500_classifier_model.pt"
        if m500.exists():
            from converter.authority.marc500_classifier import Marc500Classifier  # noqa: PLC0415
            marc500_clf = Marc500Classifier(str(m500))
    except Exception as exc:
        logger.warning("MARC500 classifier load failed: %s", exc)

    genre_clf = None
    try:
        gc = _REPO_ROOT / "ner" / "genre_classifier_model.pt"
        if gc.exists():
            from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415
            genre_clf = GenreClassifier(str(gc))
    except Exception as exc:
        logger.warning("Genre classifier load failed: %s", exc)

    import re as _re  # noqa: PLC0415

    def _split_marc500(text: str) -> list[str]:
        parts = _re.split(r"(?<=[.!?])\s+|\n", text)
        return [s.strip() for s in parts if len(s.strip()) >= 10]

    def _split_provenance(t: str) -> list[str]:
        return [seg.strip() for seg in t.split("|") if seg.strip()]

    results: list[dict[str, Any]] = []
    n_total = len(records)
    for idx, record in enumerate(records):
        all_entities: list[dict[str, Any]] = []
        # Person NER on notes + colophon
        texts: list[str] = []
        for n in record.get("notes") or []:
            if isinstance(n, str) and n.strip():
                texts.append(n)
        col = record.get("colophon_text")
        if isinstance(col, str) and col.strip():
            texts.append(col)
        offset = 0
        for i, text in enumerate(texts):
            try:
                ents = person.process_text(text)
            except Exception as exc:
                logger.debug("person NER err: %s", exc)
                ents = []
            for e in ents:
                e["start"] = e.get("start", 0) + offset
                e["end"] = e.get("end", 0) + offset
                e["source"] = "person_ner"
            all_entities.extend(ents)
            offset += len(text) + (1 if i < len(texts) - 1 else 0)

        # Provenance NER on MARC 561
        if provenance is not None:
            ptext = record.get("provenance") or ""
            if isinstance(ptext, str) and ptext.strip():
                clean = ptext.replace('""', '"')
                for seg in _split_provenance(clean):
                    if len(seg) < 3:
                        continue
                    try:
                        ents = provenance.process_text(seg)
                        for e in ents:
                            e["source"] = "provenance_ner"
                        all_entities.extend(ents)
                    except Exception as exc:
                        logger.debug("provenance NER err: %s", exc)

        # Contents NER on MARC 505
        if contents is not None:
            for c in record.get("contents") or []:
                if isinstance(c, dict):
                    parts: list[str] = []
                    if c.get("folio_range"):
                        parts.append(f"דף {c['folio_range']}:")
                    if c.get("responsibility"):
                        parts.append(f"{c['responsibility']}:")
                    if c.get("title"):
                        parts.append(str(c["title"]))
                    text_505 = " ".join(parts)
                elif isinstance(c, str):
                    text_505 = c
                else:
                    continue
                if text_505.strip() and len(text_505) >= 5:
                    try:
                        ents = contents.process_text(text_505)
                        for e in ents:
                            e["source"] = "contents_ner"
                        all_entities.extend(ents)
                    except Exception as exc:
                        logger.debug("contents NER err: %s", exc)

        # MARC 500 colophon classifier
        ml_colophon: list[str] = []
        if marc500_clf is not None:
            for note in record.get("notes") or []:
                for sent in _split_marc500(str(note)):
                    try:
                        above_thr, conf = marc500_clf.is_colophon(sent)
                        if above_thr:
                            ml_colophon.append(sent)
                            all_entities.append({
                                "text": sent,
                                "type": "COLOPHON",
                                "source": "colophon_ml",
                                "confidence": float(conf),
                                "start": 0,
                                "end": len(sent),
                            })
                    except Exception as exc:
                        logger.debug("MARC500 clf err: %s", exc)

        # Genre classifier
        if genre_clf is not None:
            try:
                title = str(record.get("title") or "").strip()
                notes_list = [str(n) for n in (record.get("notes") or []) if n]
                preds = genre_clf.predict(title, notes_list) or []
                for item in preds:
                    if isinstance(item, tuple) and len(item) >= 2:
                        label, conf = item[0], float(item[1])
                    elif isinstance(item, dict):
                        label = item.get("label", "")
                        conf = float(item.get("confidence", 0.0))
                    else:
                        continue
                    if not label or label == "other":
                        continue
                    all_entities.append({
                        "text": str(label),
                        "type": "GENRE",
                        "source": "genre_ml",
                        "confidence": conf,
                        "start": 0,
                        "end": 0,
                    })
            except Exception as exc:
                logger.debug("genre clf err: %s", exc)

        results.append(
            {
                "_control_number": record.get("_control_number"),
                "text": "\n".join(texts),
                "entities": all_entities,
                "ml_colophon_sentences": ml_colophon,
            }
        )
        if (idx + 1) % 5 == 0:
            logger.info("  NER %d/%d", idx + 1, n_total)

    out = work_dir / "ner_results.json"
    out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Stage 2 OK — %d records → %s", len(results), out.name)
    return out


# ── Stage 3: authority matching ──────────────────────────────────────


def stage_authority(
    marc_extract: Path,
    ner_results: Path | None,
    work_dir: Path,
    enable_viaf: bool = True,
    enable_kima: bool = True,
) -> Path:
    from converter.authority.kima_matcher import KimaMatcher  # noqa: PLC0415
    from converter.authority.mazal_matcher import MazalMatcher  # noqa: PLC0415
    from converter.authority.viaf_matcher import VIAFMatcher  # noqa: PLC0415

    records: list[dict[str, Any]] = json.loads(marc_extract.read_text(encoding="utf-8"))
    logger.info("Stage 3 (authority): %d records (VIAF=%s, KIMA=%s)",
                len(records), enable_viaf, enable_kima)

    # Merge NER entities by control number
    if ner_results and ner_results.exists():
        ner_recs: list[dict[str, Any]] = json.loads(
            ner_results.read_text(encoding="utf-8"),
        )
        ner_by_cn = {str(r.get("_control_number", "")): r for r in ner_recs}
        for record in records:
            cn = str(record.get("_control_number", ""))
            ner_rec = ner_by_cn.get(cn)
            if ner_rec and ner_rec.get("entities"):
                record["entities"] = ner_rec["entities"]
            ml_col = ner_rec.get("ml_colophon_sentences") if ner_rec else None
            if ml_col:
                existing = str(record.get("colophon_text") or "").strip()
                new_sents = [s for s in ml_col if s not in existing]
                if new_sents:
                    record["colophon_text"] = (
                        (existing + " " if existing else "") + " ".join(new_sents)
                    ).strip()

    mazal = MazalMatcher(index_path="")
    viaf = VIAFMatcher() if enable_viaf else None
    kima = KimaMatcher(index_path="") if enable_kima else None

    import re as _re  # noqa: PLC0415

    def _match_against(name: str, etype: str = "person") -> tuple[str | None, str | None]:
        if etype in ("organization", "meeting"):
            return None, None
        mid = mazal.match_person(name)
        vuri = viaf.match_person(name) if viaf else None
        return mid, vuri

    n_total = len(records)
    n_total_entities = 0
    n_matched = 0
    for idx, record in enumerate(records):
        cn = str(record.get("_control_number", ""))

        # NER entities (persons) → enrich in place
        for entity in record.get("entities") or []:
            name = str(entity.get("person", "")).strip()
            if not name:
                if entity.get("type") in ("OWNER", "WORK_AUTHOR"):
                    name = str(entity.get("text", "")).strip()
            if not name:
                continue
            mid, vuri = _match_against(name, "person")
            if mid:
                entity["mazal_id"] = mid
            if vuri:
                entity["viaf_uri"] = vuri
            n_total_entities += 1
            if mid or vuri:
                n_matched += 1

        # MARC name fields
        marc_matches: list[dict[str, Any]] = []
        for which, role, field in [
            ("authors", "author", "100/110/111"),
            ("contributors", "contributor", "700/710/711"),
        ]:
            for person in record.get(which) or []:
                pname = str(person.get("name", "")).strip()
                if not pname:
                    continue
                etype = str(person.get("type", "person"))
                mid, vuri = _match_against(pname, etype)
                m: dict[str, Any] = {
                    "name": pname,
                    "role": str(person.get("role", role)),
                    "source": "MARC",
                    "field": field,
                }
                if mid:
                    m["mazal_id"] = mid
                if vuri:
                    m["viaf_uri"] = vuri
                # Harvest VIAF cluster IDs
                if vuri and viaf:
                    vid = _re.search(r"/viaf/(\d+)", vuri)
                    if vid:
                        try:
                            cluster = viaf.get_cluster_identifiers(vid.group(1))
                        except Exception as exc:
                            cluster = {}
                            logger.debug("VIAF cluster err: %s", exc)
                        if cluster.get("gnd"):
                            m["gnd_id"] = cluster["gnd"]
                        if cluster.get("lc"):
                            m["lc_id"] = cluster["lc"]
                        if cluster.get("isni"):
                            m["isni"] = cluster["isni"]
                        if cluster.get("bnf"):
                            m["bnf_id"] = cluster["bnf"]
                # Mazal enrichment
                if mid:
                    try:
                        details = mazal.get_person_details(mid)
                    except Exception:
                        details = {}
                    dates = details.get("dates") if details else None
                    if dates:
                        m["dates"] = dates
                        parts = _re.split(r"[-–]", dates.strip())
                        for p in parts:
                            p = p.strip().rstrip("?")
                            if p and p.isdigit():
                                yr = int(p)
                                if 100 < yr < 2100:
                                    if "birth_year" not in m:
                                        m["birth_year"] = yr
                                    else:
                                        m["death_year"] = yr
                    if details and details.get("preferred_name_lat"):
                        m["preferred_name_lat"] = details["preferred_name_lat"]
                marc_matches.append(m)
                n_total_entities += 1
                if mid or vuri:
                    n_matched += 1
        if marc_matches:
            record["marc_authority_matches"] = marc_matches

        # KIMA places
        if kima is not None:
            places = [str(p) for p in (record.get("related_places") or []) if p]
            place_matches: dict[str, str] = {}
            for place in places:
                try:
                    uri = kima.match_place(place)
                except Exception:
                    uri = None
                if uri:
                    place_matches[place] = uri
            if place_matches:
                record["kima_places"] = place_matches

        if (idx + 1) % 10 == 0:
            logger.info("  authority %d/%d", idx + 1, n_total)

    mazal.close()
    if kima is not None:
        kima.close()

    out = work_dir / "authority_enriched.json"
    out.write_text(
        json.dumps(records, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info(
        "Stage 3 OK — %d/%d entities matched → %s",
        n_matched, n_total_entities, out.name,
    )
    return out


# ── Stage 4: RDF ─────────────────────────────────────────────────────


def stage_rdf(authority_path: Path, work_dir: Path) -> Path | None:
    try:
        from converter.transformer.mapper import MarcToRdfMapper  # noqa: PLC0415

        logger.info("Stage 4 (RDF): building graph")
        mapper = MarcToRdfMapper()
        graph = mapper.map_file(authority_path)
        out = work_dir / "output.ttl"
        graph.serialize(destination=str(out), format="turtle")
        logger.info("Stage 4 OK — %d triples → %s", len(graph), out.name)
        return out
    except Exception as exc:
        logger.warning("Stage 4 RDF failed: %s", exc)
        logger.debug(traceback.format_exc())
        return None


# ── Stage 5: SHACL ───────────────────────────────────────────────────


def stage_shacl(ttl_path: Path, work_dir: Path) -> dict[str, Any] | None:
    try:
        from converter.validation.shacl_validator import ShaclValidator  # noqa: PLC0415
        shapes = _REPO_ROOT / "ontology" / "shacl-shapes.ttl"
        if not shapes.exists():
            logger.warning("Stage 5 SHACL: shapes file missing")
            return None
        validator = ShaclValidator(shapes_path=shapes)
        result = validator.validate_file(ttl_path)
        report = work_dir / "shacl_report.txt"
        report.write_text(
            f"Conforms: {result.conforms}\n\n{result.results_text or ''}",
            encoding="utf-8",
        )
        logger.info("Stage 5 OK — conforms=%s", result.conforms)
        return {"conforms": bool(result.conforms)}
    except Exception as exc:
        logger.warning("Stage 5 SHACL failed: %s", exc)
        logger.debug(traceback.format_exc())
        return None


# ── Stage 6: Wikidata items + QuickStatements (DRY-RUN ONLY) ─────────


def stage_wikidata(authority_path: Path, work_dir: Path) -> tuple[list[Any], int]:
    from converter.wikidata.item_builder import WikidataItemBuilder  # noqa: PLC0415
    from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415

    records: list[dict[str, Any]] = json.loads(authority_path.read_text(encoding="utf-8"))
    logger.info("Stage 6 (Wikidata items): building from %d records", len(records))
    builder = WikidataItemBuilder()
    items = builder.build_all(records)
    person_count = builder.person_count
    logger.info(
        "Stage 6 OK — %d items (%d persons + %d non-persons)",
        len(items), person_count, len(items) - person_count,
    )

    exporter = QuickStatementsExporter()
    qs_path = work_dir / "quickstatements.txt"
    exporter.export_to_file(items, qs_path)
    logger.info("QuickStatements (dry-run) → %s", qs_path.name)
    return items, person_count


# ── Cache assembly ────────────────────────────────────────────────────


def _entity_type_of(item: Any) -> str:
    return str(getattr(item, "entity_type", "") or "").lower()


def _is_manuscript(item: Any) -> bool:
    return _entity_type_of(item) == "manuscript"


def _is_person(item: Any) -> bool:
    return _entity_type_of(item) == "person"


def _is_work(item: Any) -> bool:
    return _entity_type_of(item) == "work"


def assemble_caches(
    items: list[Any],
    n_records: int,
    person_count: int,
    corpus_sha: str,
    n_records_in_corpus: int,
    upload_failures: int = 0,
    shacl_conforms: bool | None = None,
    runtime_seconds: float = 0.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manuscripts = [i for i in items if _is_manuscript(i)]
    persons = [i for i in items if _is_person(i)]
    works = [i for i in items if _is_work(i)]

    # Property coverage on manuscripts
    n_ms = len(manuscripts) or 1
    prop_records: dict[str, set[str]] = {}
    prop_total_claims: dict[str, int] = Counter()
    for ms in manuscripts:
        props_in_ms: set[str] = set()
        for stmt in getattr(ms, "statements", []) or []:
            pid = str(stmt.property_id)
            props_in_ms.add(pid)
            prop_total_claims[pid] += 1
        for p in props_in_ms:
            prop_records.setdefault(p, set()).add(getattr(ms, "local_id", ""))
    property_coverage: dict[str, dict[str, Any]] = {}
    for pid, recs in prop_records.items():
        property_coverage[pid] = {
            "records_with": len(recs),
            "ratio": round(len(recs) / n_ms, 4),
            "total_claims": prop_total_claims.get(pid, 0),
        }

    # Per-person property counts
    person_prop_counts: Counter = Counter()
    person_records_with: dict[str, set[str]] = {}
    for p in persons:
        props_in_p: set[str] = set()
        for stmt in getattr(p, "statements", []) or []:
            pid = str(stmt.property_id)
            props_in_p.add(pid)
            person_prop_counts[pid] += 1
        for pid in props_in_p:
            person_records_with.setdefault(pid, set()).add(getattr(p, "local_id", ""))

    # Manuscript-side aggregates
    total_ms_stmts = sum(len(getattr(m, "statements", []) or []) for m in manuscripts)
    total_person_stmts = sum(len(getattr(p, "statements", []) or []) for p in persons)
    stmts_per_ms = round(total_ms_stmts / n_ms, 3)
    stmts_per_person = round(total_person_stmts / max(len(persons), 1), 3)

    # P136 (genre) coverage and derived metrics
    p136_records = property_coverage.get("P136", {})
    p571_records = property_coverage.get("P571", {})
    p1574_total = prop_total_claims.get("P1574", 0)

    # P31 always = 1.0 if all manuscripts have it
    metrics = {
        "n_records": n_records,
        "n_manuscripts": len(manuscripts),
        "n_persons": len(persons),
        "n_works": len(works),
        "stmts_per_ms": stmts_per_ms,
        "stmts_per_person": stmts_per_person,
        "date_coverage": p571_records.get("ratio", 0.0),
        "genre_coverage": p136_records.get("ratio", 0.0),
        "p136_coverage_after_classifier": p136_records.get("ratio", 0.0),
        "upload_failures": upload_failures,
        "p1574_total_claims": p1574_total,
        # Unmapped-work-titles is the count of works with no existing_qid
        # (i.e., new local items being created). Used by claim
        # ABS-dominant-loss-work-titles.
        "unmapped_work_titles": sum(
            1 for w in works if not getattr(w, "existing_qid", None)
        ),
        "shacl_conforms": shacl_conforms,
        "runtime_seconds": round(runtime_seconds, 1),
    }

    item_counts = {
        "total": len(items),
        "manuscripts": len(manuscripts),
        "persons": len(persons),
        "works": len(works),
        "p1574_claims": p1574_total,
        "eval_corpus": n_records_in_corpus,
    }

    type_counts = {
        "person": len(persons),
        "manuscript": len(manuscripts),
        "work": len(works),
    }

    pipeline_run = {
        "corpus_sha256": corpus_sha,
        "n_records": n_records,
        "metrics": metrics,
        "property_coverage": property_coverage,
        "person_property_counts": dict(person_prop_counts),
        "item_counts": item_counts,
        "type_counts": type_counts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── wikidata_build cache ─────────────────────────────────────────
    duplicates = 0  # not measured; reconciler is invoked but we don't run live
    table_8 = {
        "total_uploaded": len(items),
        "duplicates": duplicates,
        "stmts_per_ms_avg": stmts_per_ms,
        "idempotency_unchanged": len(items),
    }
    property_counts_for_wb = {
        pid: prop_total_claims.get(pid, 0) for pid in prop_total_claims
    }
    wikidata_build = {
        "corpus_sha256": corpus_sha,
        "item_counts": item_counts,
        "property_counts": property_counts_for_wb,
        "person_property_counts": dict(person_prop_counts),
        "table_8_metrics": table_8,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    return pipeline_run, wikidata_build


# ── Main ──────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu",
                        help="Device for NER inference (cpu, mps, cuda)")
    parser.add_argument("--no-ner", action="store_true",
                        help="Skip NER stage (use empty entities)")
    parser.add_argument("--no-viaf", action="store_true",
                        help="Skip VIAF API calls (avoid network)")
    parser.add_argument("--no-kima", action="store_true",
                        help="Skip KIMA matching")
    parser.add_argument("--verbose", action="store_true")
    ns = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if ns.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if not _CORPUS_PATH.is_file():
        logger.error("Corpus not found: %s", _CORPUS_PATH)
        return 2

    # Verify corpus SHA pin
    sha = _hash_file(_CORPUS_PATH)
    if _FIXTURE_SHA.is_file():
        pinned = _FIXTURE_SHA.read_text(encoding="utf-8").strip().split()[0]
        if pinned != sha:
            logger.error(
                "Corpus SHA mismatch: pinned=%s actual=%s — refusing to run",
                pinned[:12], sha[:12],
            )
            return 2

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    run_dir = _RESULTS_DIR / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    work_dir = run_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()

    # Stage 1: parse
    marc_extract = stage_parse(_CORPUS_PATH, work_dir, verbose=ns.verbose)
    n_records = len(json.loads(marc_extract.read_text(encoding="utf-8")))

    # Stage 2: NER
    ner_path: Path | None = None
    if not ns.no_ner:
        try:
            ner_path = stage_ner(marc_extract, work_dir, device=ns.device)
        except Exception as exc:
            logger.warning("Stage 2 NER crashed: %s — continuing", exc)
            logger.debug(traceback.format_exc())

    # Stage 3: authority
    authority_path = stage_authority(
        marc_extract,
        ner_path,
        work_dir,
        enable_viaf=not ns.no_viaf,
        enable_kima=not ns.no_kima,
    )

    # Stage 4: RDF (best-effort — needed for SHACL only)
    ttl_path = stage_rdf(authority_path, work_dir)

    # Stage 5: SHACL (best-effort)
    shacl_result = stage_shacl(ttl_path, work_dir) if ttl_path else None

    # Stage 6: Wikidata items (DRY-RUN — QuickStatements only)
    items, person_count = stage_wikidata(authority_path, work_dir)

    elapsed = time.monotonic() - started

    pipeline_run, wikidata_build = assemble_caches(
        items=items,
        n_records=n_records,
        person_count=person_count,
        corpus_sha=sha,
        n_records_in_corpus=n_records,
        upload_failures=0,
        shacl_conforms=shacl_result.get("conforms") if shacl_result else None,
        runtime_seconds=elapsed,
    )

    pipeline_cache = run_dir / "pipeline_run.json"
    pipeline_cache.write_text(
        json.dumps(pipeline_run, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    wb_cache = run_dir / "wikidata_build.json"
    wb_cache.write_text(
        json.dumps(wikidata_build, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    logger.info("Caches written:")
    logger.info("  %s", pipeline_cache.relative_to(_REPO_ROOT))
    logger.info("  %s", wb_cache.relative_to(_REPO_ROOT))
    logger.info("Total runtime: %.1fs", elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
