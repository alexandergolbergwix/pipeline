"""
Comprehensive audit of 98 Hebrew manuscripts uploaded to Wikidata.

Fetches all items via wbgetentities API (50 at a time), checks each item
for correctness and completeness, and writes a JSON report.
"""

import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
QIDS_FILE = Path("/tmp/ms_qids.json")
OUTPUT_FILE = Path("/Users/alexandergo/Desktop/ner9/100/wikidata_audit_report.json")
API_URL = "https://www.wikidata.org/w/api.php"
BATCH_SIZE = 50
SLEEP_BETWEEN = 1.0  # seconds between API calls

# Expected values
EXPECTED_P31 = {"Q87167", "Q48498", "Q213924"}  # manuscript, codex, illuminated manuscript
EXPECTED_P195 = "Q188915"   # NLI
EXPECTED_P282 = "Q33513"    # Hebrew alphabet (NOT Q9623)
EXPECTED_P5008 = "Q123078816"  # focus list (NOT Q123476064)
EXPECTED_P407 = {"Q9288"}   # Hebrew; we'll also flag others
# NLI Alma system IDs: 18 digits, start with "99", end with "05171"
# (Old legacy format was 98xxxxx5171, 11 digits — no longer used)
P8189_ALMA_PREFIX = "99"
P8189_ALMA_SUFFIX = "05171"
P8189_ALMA_LENGTH = 18

# Properties we check
PROPERTY_LABELS: dict[str, str] = {
    "P31":    "instance of",
    "P195":   "collection",
    "P217":   "inventory number (shelfmark)",
    "P8189":  "NLI J9U ID",
    "P1476":  "title",
    "P407":   "language of work",
    "P282":   "writing system",
    "P571":   "inception",
    "P1071":  "location of creation",
    "P186":   "material used",
    "P5008":  "on focus list of Wikimedia project",
    "P50":    "author",
    "P11603": "transcribed by (scribe)",
    "P127":   "owned by",
}

REQUIRED_PROPERTIES = {"P31", "P195", "P217", "P8189", "P1476", "P407", "P282", "P571"}
IMPORTANT_PROPERTIES = {"P1071", "P186", "P5008", "P50", "P11603", "P127"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_entities(qids: list[str]) -> dict:
    """Fetch entity data from Wikidata API for a list of QIDs."""
    params = {
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "format": "json",
        "props": "claims|labels|descriptions",
        "languages": "en|he",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "MHM-Pipeline-Audit/1.0 (research; mailto:alex@example.com)")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_claim_values(claims: dict, prop: str) -> list[dict]:
    """Extract all claim values for a property, returning list of dicts with value info."""
    results = []
    if prop not in claims:
        return results
    for claim in claims[prop]:
        entry: dict = {"rank": claim.get("rank", "normal"), "has_references": False, "references": []}

        # Check references
        refs = claim.get("references", [])
        if refs:
            entry["has_references"] = True
            for ref in refs:
                ref_snaks = ref.get("snaks", {})
                ref_props = list(ref_snaks.keys())
                entry["references"].append(ref_props)

        mainsnak = claim.get("mainsnak", {})
        snak_type = mainsnak.get("snaktype", "")
        if snak_type == "novalue":
            entry["type"] = "novalue"
            results.append(entry)
            continue
        if snak_type == "somevalue":
            entry["type"] = "somevalue"
            results.append(entry)
            continue

        datavalue = mainsnak.get("datavalue", {})
        vtype = datavalue.get("type", "")
        value = datavalue.get("value", {})

        if vtype == "wikibase-entityid":
            entry["type"] = "entity"
            entry["id"] = value.get("id", "")
        elif vtype == "string":
            entry["type"] = "string"
            entry["value"] = value
        elif vtype == "monolingualtext":
            entry["type"] = "monolingualtext"
            entry["text"] = value.get("text", "")
            entry["language"] = value.get("language", "")
        elif vtype == "time":
            entry["type"] = "time"
            entry["time"] = value.get("time", "")
            entry["precision"] = value.get("precision", 0)
        elif vtype == "quantity":
            entry["type"] = "quantity"
            entry["amount"] = value.get("amount", "")
        else:
            entry["type"] = vtype
            entry["raw"] = value

        results.append(entry)
    return results


def get_label(entity_data: dict, lang: str = "en") -> str:
    """Get label for an entity in a given language."""
    labels = entity_data.get("labels", {})
    if lang in labels:
        return labels[lang].get("value", "")
    if "he" in labels:
        return labels["he"].get("value", "")
    return ""


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------

def main() -> None:
    qids: list[str] = json.loads(QIDS_FILE.read_text())
    print(f"Loaded {len(qids)} QIDs from {QIDS_FILE}")

    # Fetch all entities in batches
    all_entities: dict = {}
    for i in range(0, len(qids), BATCH_SIZE):
        batch = qids[i:i + BATCH_SIZE]
        print(f"Fetching batch {i // BATCH_SIZE + 1}: QIDs {i + 1}–{i + len(batch)} ...")
        data = fetch_entities(batch)
        entities = data.get("entities", {})
        all_entities.update(entities)
        if i + BATCH_SIZE < len(qids):
            time.sleep(SLEEP_BETWEEN)

    print(f"Fetched {len(all_entities)} entities total.\n")

    # Audit each entity
    property_coverage: dict[str, int] = defaultdict(int)  # prop -> count of items that have it
    wrong_qids: list[dict] = []
    missing_props: dict[str, list[str]] = defaultdict(list)  # prop -> list of QIDs missing it
    unreferenced: dict[str, list[str]] = defaultdict(list)  # QID -> list of props with unreferenced claims
    total_claims = 0
    total_referenced = 0
    item_details: dict[str, dict] = {}  # QID -> full details dict

    # Collect all entity QIDs referenced (for label lookup)
    referenced_entity_ids: set[str] = set()

    for qid in qids:
        entity = all_entities.get(qid)
        if entity is None:
            print(f"  WARNING: {qid} not found in API response!")
            continue

        claims = entity.get("claims", {})
        label = get_label(entity)
        detail: dict = {
            "qid": qid,
            "label": label,
            "properties": {},
            "issues": [],
        }

        # Check each property
        for prop, prop_label in PROPERTY_LABELS.items():
            values = get_claim_values(claims, prop)
            if values:
                property_coverage[prop] += 1
                detail["properties"][prop] = {
                    "label": prop_label,
                    "values": values,
                }
            else:
                if prop in REQUIRED_PROPERTIES:
                    missing_props[prop].append(qid)
                    detail["issues"].append(f"MISSING required: {prop} ({prop_label})")
                elif prop in IMPORTANT_PROPERTIES:
                    missing_props[prop].append(qid)

            # Count references
            for v in values:
                total_claims += 1
                if v["has_references"]:
                    total_referenced += 1
                else:
                    unreferenced[qid].append(prop)

        # --- Specific correctness checks ---

        # P31: instance of
        p31_vals = get_claim_values(claims, "P31")
        p31_ids = {v.get("id", "") for v in p31_vals if v.get("type") == "entity"}
        if not p31_ids & EXPECTED_P31:
            wrong_qids.append({
                "qid": qid,
                "label": label,
                "property": "P31",
                "issue": f"instance of = {p31_ids}, expected one of {EXPECTED_P31}",
            })
            detail["issues"].append(f"P31 unexpected: {p31_ids}")

        # P195: collection
        p195_vals = get_claim_values(claims, "P195")
        p195_ids = {v.get("id", "") for v in p195_vals if v.get("type") == "entity"}
        if p195_vals and EXPECTED_P195 not in p195_ids:
            wrong_qids.append({
                "qid": qid,
                "label": label,
                "property": "P195",
                "issue": f"collection = {p195_ids}, expected {EXPECTED_P195}",
            })
            detail["issues"].append(f"P195 unexpected: {p195_ids}")

        # P282: writing system (should be Q33513, NOT Q9623)
        p282_vals = get_claim_values(claims, "P282")
        p282_ids = {v.get("id", "") for v in p282_vals if v.get("type") == "entity"}
        if p282_vals:
            if "Q9623" in p282_ids:
                wrong_qids.append({
                    "qid": qid,
                    "label": label,
                    "property": "P282",
                    "issue": f"writing system = Q9623 (Hebrew language), should be Q33513 (Hebrew alphabet)",
                })
                detail["issues"].append("P282 WRONG: Q9623 (Hebrew language) instead of Q33513 (Hebrew alphabet)")
            elif EXPECTED_P282 not in p282_ids:
                wrong_qids.append({
                    "qid": qid,
                    "label": label,
                    "property": "P282",
                    "issue": f"writing system = {p282_ids}, expected {EXPECTED_P282}",
                })
                detail["issues"].append(f"P282 unexpected: {p282_ids}")

        # P5008: focus list
        p5008_vals = get_claim_values(claims, "P5008")
        p5008_ids = {v.get("id", "") for v in p5008_vals if v.get("type") == "entity"}
        if p5008_vals:
            if "Q123476064" in p5008_ids:
                wrong_qids.append({
                    "qid": qid,
                    "label": label,
                    "property": "P5008",
                    "issue": f"focus list = Q123476064 (wrong), should be Q123078816",
                })
                detail["issues"].append("P5008 WRONG: Q123476064 instead of Q123078816")
            elif EXPECTED_P5008 not in p5008_ids:
                wrong_qids.append({
                    "qid": qid,
                    "label": label,
                    "property": "P5008",
                    "issue": f"focus list = {p5008_ids}, expected {EXPECTED_P5008}",
                })

        # P8189: NLI ID format check (Alma format: 18 digits, starts 99, ends 05171)
        p8189_vals = get_claim_values(claims, "P8189")
        for v in p8189_vals:
            if v.get("type") == "string":
                nli_id = v.get("value", "")
                is_alma = (
                    nli_id.startswith(P8189_ALMA_PREFIX)
                    and nli_id.endswith(P8189_ALMA_SUFFIX)
                    and len(nli_id) == P8189_ALMA_LENGTH
                    and nli_id.isdigit()
                )
                if not is_alma:
                    wrong_qids.append({
                        "qid": qid,
                        "label": label,
                        "property": "P8189",
                        "issue": f"NLI ID '{nli_id}' does not match Alma format 99XXXXXXXXXXXX05171 (18 digits)",
                    })
                    detail["issues"].append(f"P8189 format: '{nli_id}' unexpected format")

        # P407: language
        p407_vals = get_claim_values(claims, "P407")
        p407_ids = {v.get("id", "") for v in p407_vals if v.get("type") == "entity"}
        if p407_vals:
            detail["properties"].setdefault("P407_ids", list(p407_ids))

        # Collect referenced entity IDs for author, scribe, owner
        for prop in ("P50", "P11603", "P127"):
            vals = get_claim_values(claims, prop)
            for v in vals:
                if v.get("type") == "entity":
                    referenced_entity_ids.add(v["id"])

        # Also count any extra properties not in our check list
        extra_props = set(claims.keys()) - set(PROPERTY_LABELS.keys())
        if extra_props:
            detail["extra_properties"] = sorted(extra_props)

        item_details[qid] = detail

    # -----------------------------------------------------------------------
    # Fetch labels for referenced people entities
    # -----------------------------------------------------------------------
    person_labels: dict[str, str] = {}
    ref_ids = sorted(referenced_entity_ids)
    if ref_ids:
        print(f"Fetching labels for {len(ref_ids)} referenced entities ...")
        for i in range(0, len(ref_ids), BATCH_SIZE):
            batch = ref_ids[i:i + BATCH_SIZE]
            data = fetch_entities(batch)
            for eid, edata in data.get("entities", {}).items():
                person_labels[eid] = get_label(edata)
            if i + BATCH_SIZE < len(ref_ids):
                time.sleep(SLEEP_BETWEEN)

    # Enrich item details with person labels
    for qid, detail in item_details.items():
        for prop in ("P50", "P11603", "P127"):
            prop_data = detail["properties"].get(prop)
            if prop_data:
                for v in prop_data["values"]:
                    if v.get("type") == "entity" and v.get("id") in person_labels:
                        v["label"] = person_labels[v["id"]]

    # -----------------------------------------------------------------------
    # Extra property analysis (properties present that we didn't check)
    # -----------------------------------------------------------------------
    extra_property_counts: dict[str, int] = defaultdict(int)
    for qid, detail in item_details.items():
        for p in detail.get("extra_properties", []):
            extra_property_counts[p] += 1

    # Fetch labels for entity QIDs used in P31, P407, P186, P1071
    entity_qids_to_label: set[str] = set()
    for qid, detail in item_details.items():
        for prop in ("P31", "P407", "P186", "P1071", "P282", "P5008"):
            pdata = detail["properties"].get(prop)
            if pdata:
                for v in pdata.get("values", []):
                    if v.get("type") == "entity":
                        entity_qids_to_label.add(v["id"])
    # Also fetch labels for extra properties (they are property IDs like P9302)
    extra_prop_entity_ids = sorted(entity_qids_to_label - set(person_labels.keys()))
    entity_labels: dict[str, str] = dict(person_labels)  # start with what we have
    if extra_prop_entity_ids:
        print(f"Fetching labels for {len(extra_prop_entity_ids)} value entities ...")
        for i in range(0, len(extra_prop_entity_ids), BATCH_SIZE):
            batch = extra_prop_entity_ids[i:i + BATCH_SIZE]
            data = fetch_entities(batch)
            for eid, edata in data.get("entities", {}).items():
                entity_labels[eid] = get_label(edata)
            if i + BATCH_SIZE < len(extra_prop_entity_ids):
                time.sleep(SLEEP_BETWEEN)

    # -----------------------------------------------------------------------
    # Build report
    # -----------------------------------------------------------------------
    total_items = len(qids)

    # Property coverage table
    coverage_table: dict[str, dict] = {}
    for prop, prop_label in PROPERTY_LABELS.items():
        count = property_coverage.get(prop, 0)
        coverage_table[prop] = {
            "label": prop_label,
            "count": count,
            "total": total_items,
            "percentage": round(100 * count / total_items, 1),
            "required": prop in REQUIRED_PROPERTIES,
        }

    # Wrong QIDs summary
    wrong_by_prop: dict[str, list[dict]] = defaultdict(list)
    for w in wrong_qids:
        wrong_by_prop[w["property"]].append(w)

    # Unreferenced summary
    items_with_unreferenced = {qid for qid, props in unreferenced.items() if props}
    unreferenced_by_prop: dict[str, int] = defaultdict(int)
    for qid, props in unreferenced.items():
        for p in props:
            unreferenced_by_prop[p] += 1

    # Missing required properties
    missing_required_summary: dict[str, dict] = {}
    for prop in REQUIRED_PROPERTIES:
        missing_list = missing_props.get(prop, [])
        if missing_list:
            missing_required_summary[prop] = {
                "label": PROPERTY_LABELS[prop],
                "count_missing": len(missing_list),
                "qids": missing_list[:20],  # show first 20
            }

    # Missing important (optional) properties
    missing_important_summary: dict[str, dict] = {}
    for prop in IMPORTANT_PROPERTIES:
        missing_list = missing_props.get(prop, [])
        missing_important_summary[prop] = {
            "label": PROPERTY_LABELS[prop],
            "count_missing": len(missing_list),
            "count_present": total_items - len(missing_list),
            "percentage_present": round(100 * (total_items - len(missing_list)) / total_items, 1),
        }

    # Sample 5 manuscripts with full detail
    sample_qids = qids[:5]
    sample_details = [item_details[q] for q in sample_qids if q in item_details]

    # Items with issues
    items_with_issues = {qid: d for qid, d in item_details.items() if d["issues"]}

    # Author/scribe/owner analysis
    author_qids: dict[str, int] = defaultdict(int)
    scribe_qids: dict[str, int] = defaultdict(int)
    owner_qids: dict[str, int] = defaultdict(int)
    for qid, detail in item_details.items():
        for v in detail["properties"].get("P50", {}).get("values", []):
            if v.get("type") == "entity":
                key = f"{v['id']} ({v.get('label', '?')})"
                author_qids[key] += 1
        for v in detail["properties"].get("P11603", {}).get("values", []):
            if v.get("type") == "entity":
                key = f"{v['id']} ({v.get('label', '?')})"
                scribe_qids[key] += 1
        for v in detail["properties"].get("P127", {}).get("values", []):
            if v.get("type") == "entity":
                key = f"{v['id']} ({v.get('label', '?')})"
                owner_qids[key] += 1

    # Language analysis
    language_counts: dict[str, int] = defaultdict(int)
    for qid, detail in item_details.items():
        p407_data = detail["properties"].get("P407")
        if p407_data:
            for v in p407_data["values"]:
                if v.get("type") == "entity":
                    language_counts[v["id"]] += 1

    # Material analysis
    material_counts: dict[str, int] = defaultdict(int)
    for qid, detail in item_details.items():
        p186_data = detail["properties"].get("P186")
        if p186_data:
            for v in p186_data["values"]:
                if v.get("type") == "entity":
                    material_counts[v["id"]] += 1

    # P31 analysis
    p31_counts: dict[str, int] = defaultdict(int)
    for qid, detail in item_details.items():
        p31_data = detail["properties"].get("P31")
        if p31_data:
            for v in p31_data["values"]:
                if v.get("type") == "entity":
                    p31_counts[v["id"]] += 1

    # -----------------------------------------------------------------------
    # Assemble final report
    # -----------------------------------------------------------------------
    report = {
        "summary": {
            "total_manuscripts": total_items,
            "total_fetched": len(all_entities),
            "audit_date": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "total_claims_checked": total_claims,
            "total_referenced_claims": total_referenced,
            "total_unreferenced_claims": total_claims - total_referenced,
            "reference_rate_percent": round(100 * total_referenced / total_claims, 1) if total_claims else 0,
            "items_with_issues": len(items_with_issues),
            "items_without_issues": total_items - len(items_with_issues),
        },
        "property_coverage": coverage_table,
        "wrong_qids": {
            "total_issues": len(wrong_qids),
            "by_property": {
                prop: {
                    "count": len(issues),
                    "items": issues,
                }
                for prop, issues in sorted(wrong_by_prop.items())
            },
        },
        "missing_required_properties": missing_required_summary,
        "missing_important_properties": missing_important_summary,
        "unreferenced_statements": {
            "items_with_unreferenced_claims": len(items_with_unreferenced),
            "by_property": dict(sorted(unreferenced_by_prop.items(), key=lambda x: -x[1])),
            "percentage_items_with_unreferenced": round(
                100 * len(items_with_unreferenced) / total_items, 1
            ) if total_items else 0,
        },
        "value_analysis": {
            "P31_instance_of": {
                qid_: {"count": cnt, "label": entity_labels.get(qid_, qid_)}
                for qid_, cnt in sorted(p31_counts.items(), key=lambda x: -x[1])
            },
            "P407_languages": {
                qid_: {"count": cnt, "label": entity_labels.get(qid_, qid_)}
                for qid_, cnt in sorted(language_counts.items(), key=lambda x: -x[1])
            },
            "P186_materials": {
                qid_: {"count": cnt, "label": entity_labels.get(qid_, qid_)}
                for qid_, cnt in sorted(material_counts.items(), key=lambda x: -x[1])
            },
            "P50_authors": dict(sorted(author_qids.items(), key=lambda x: -x[1])),
            "P11603_scribes": dict(sorted(scribe_qids.items(), key=lambda x: -x[1])),
            "P127_owners": dict(sorted(owner_qids.items(), key=lambda x: -x[1])),
        },
        "extra_properties_found": {
            prop: extra_property_counts[prop]
            for prop in sorted(extra_property_counts, key=lambda p: -extra_property_counts[p])
        },
        "items_with_issues": {
            qid: detail["issues"]
            for qid, detail in sorted(items_with_issues.items())
        },
        "sample_5_manuscripts": sample_details,
        "all_item_summaries": {
            qid: {
                "label": detail["label"],
                "issue_count": len(detail["issues"]),
                "property_count": len(detail["properties"]),
                "issues": detail["issues"] if detail["issues"] else None,
            }
            for qid, detail in sorted(item_details.items())
        },
    }

    # Write report
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport saved to {OUTPUT_FILE}")

    # -----------------------------------------------------------------------
    # Print summary to stdout
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("WIKIDATA MANUSCRIPT AUDIT REPORT")
    print("=" * 70)
    print(f"Total manuscripts: {total_items}")
    print(f"Items with issues: {len(items_with_issues)}")
    print(f"Total claims checked: {total_claims}")
    print(f"Referenced claims: {total_referenced} ({report['summary']['reference_rate_percent']}%)")
    print(f"Unreferenced claims: {total_claims - total_referenced}")

    print("\n--- PROPERTY COVERAGE ---")
    for prop in PROPERTY_LABELS:
        info = coverage_table[prop]
        marker = " [REQUIRED]" if info["required"] else ""
        print(f"  {prop} ({info['label']}): {info['count']}/{info['total']} = {info['percentage']}%{marker}")

    print(f"\n--- WRONG QIDs ({len(wrong_qids)} issues) ---")
    for prop, issues in sorted(wrong_by_prop.items()):
        print(f"  {prop}: {len(issues)} items")
        for w in issues[:5]:
            print(f"    {w['qid']}: {w['issue']}")
        if len(issues) > 5:
            print(f"    ... and {len(issues) - 5} more")

    print(f"\n--- MISSING REQUIRED PROPERTIES ---")
    for prop, info in sorted(missing_required_summary.items()):
        print(f"  {prop} ({info['label']}): {info['count_missing']} missing")

    print(f"\n--- IMPORTANT PROPERTY PRESENCE ---")
    for prop, info in sorted(missing_important_summary.items()):
        print(f"  {prop} ({info['label']}): {info['count_present']}/{total_items} = {info['percentage_present']}%")

    print(f"\n--- VALUE ANALYSIS ---")
    print("  P31 (instance of):")
    for qid_, cnt in sorted(p31_counts.items(), key=lambda x: -x[1]):
        print(f"    {qid_} ({entity_labels.get(qid_, '?')}): {cnt}")
    print("  P407 (language):")
    for qid_, cnt in sorted(language_counts.items(), key=lambda x: -x[1]):
        print(f"    {qid_} ({entity_labels.get(qid_, '?')}): {cnt}")
    print("  P186 (material):")
    for qid_, cnt in sorted(material_counts.items(), key=lambda x: -x[1]):
        print(f"    {qid_} ({entity_labels.get(qid_, '?')}): {cnt}")
    print(f"  P50 authors: {len(author_qids)} distinct")
    print(f"  P11603 scribes: {len(scribe_qids)} distinct")
    print(f"  P127 owners: {len(owner_qids)} distinct")

    print(f"\n--- EXTRA PROPERTIES (not in audit list) ---")
    for prop, count in sorted(extra_property_counts.items(), key=lambda x: -x[1]):
        print(f"  {prop}: found on {count}/{total_items} items")

    print(f"\n--- UNREFERENCED BY PROPERTY ---")
    for prop, count in sorted(unreferenced_by_prop.items(), key=lambda x: -x[1])[:10]:
        pname = PROPERTY_LABELS.get(prop, prop)
        print(f"  {prop} ({pname}): {count} unreferenced claims")

    # Print location of creation analysis
    p1071_entities: dict[str, int] = defaultdict(int)
    for qid_, detail in item_details.items():
        p1071_data = detail["properties"].get("P1071")
        if p1071_data:
            for v in p1071_data["values"]:
                if v.get("type") == "entity":
                    eid = v["id"]
                    lbl = entity_labels.get(eid, eid)
                    p1071_entities[f"{eid} ({lbl})"] += 1
    if p1071_entities:
        print(f"\n--- LOCATION OF CREATION (P1071) - top 15 ---")
        for loc, cnt in sorted(p1071_entities.items(), key=lambda x: -x[1])[:15]:
            print(f"  {loc}: {cnt}")

    print("\nDone.")


if __name__ == "__main__":
    main()
