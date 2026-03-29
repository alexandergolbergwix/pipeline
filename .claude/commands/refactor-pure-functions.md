# Refactor to Pure Functions

Refactor deeply nested if statements to use pure functions and predicate-based logic.

## Guidelines

1. **Replace nested conditionals with predicate functions:**
   - `should_handle(entity)` instead of `if entity and entity.get("name") and entity.get("type") == "PERSON"`
   - `is_valid_record(record)` instead of checking multiple fields inline
   - `has_authority_data(matcher)` instead of `if matcher and matcher.is_available`

2. **Use early returns with guards:**
   ```python
   # Before: deeply nested
   def process(data):
       if data:
           if data.get("items"):
               for item in data["items"]:
                   if item.get("active"):
                       process_item(item)

   # After: flat with guards
   def process(data):
       if not data:
           return
       if not data.get("items"):
           return
       for item in data["items"]:
           if not item.get("active"):
               continue
           process_item(item)
   ```

3. **Extract transformation logic to pure functions:**
   ```python
   def transform_entity(entity):
       return {
           "name": entity.get("name", "").strip(),
           "role": normalize_role(entity.get("role")),
           "confidence": float(entity.get("confidence", 0)),
       }
   ```

4. **Use functional patterns:**
   - `filter(is_valid, items)` instead of list comprehension with inline conditions
   - `map(transform, items)` for data transformations
   - `groupby(key_func, items)` for categorization

## Procedure

1. Identify files with deeply nested conditionals (3+ levels)
2. Extract predicate functions for common checks
3. Flatten nested structures with guards and early returns
4. Extract transformation logic to pure functions
5. Update tests to verify the refactored functions

## Example Refactoring

**Before:**
```python
for entity in entities:
    if entity:
        name = entity.get("person")
        if name:
            name = name.strip()
            if name:
                matcher = get_matcher()
                if matcher and matcher.is_available:
                    result = matcher.match(name)
                    if result:
                        entity["match"] = result
```

**After:**
```python
def should_match_entity(entity):
    if not entity:
        return False
    name = entity.get("person", "").strip()
    return bool(name)

def match_entity(entity, matcher):
    if not matcher or not matcher.is_available:
        return None
    return matcher.match(entity.get("person").strip())

for entity in filter(should_match_entity, entities):
    result = match_entity(entity, get_matcher())
    if result:
        entity["match"] = result
```

## Real-World Example from Codebase

**AuthorityWorker.run() refactored structure:**

```python
class AuthorityWorker(StageWorker):
    # ── Pure predicate functions ───────────────────────────────────────
    @staticmethod
    def _has_valid_name(data: dict, key: str) -> bool:
        """Check if entity has a valid non-empty name."""
        name = str(data.get(key, "")).strip()
        return bool(name)

    @staticmethod
    def _is_already_matched(match_info: dict) -> bool:
        """Check if entity already has at least one authority match."""
        return bool(match_info.get("mazal_id") or match_info.get("viaf_uri"))

    # ── Pure transformation functions ──────────────────────────────────
    @staticmethod
    def _create_match_info(
        name: str,
        role: str,
        source: str,
        field: str,
        mazal_id: str | None,
        viaf_uri: str | None,
    ) -> dict[str, object]:
        """Create a match info dictionary with authority references."""
        info: dict[str, object] = {
            "name": name,
            "role": role,
            "source": source,
            "field": field,
        }
        if mazal_id:
            info["mazal_id"] = mazal_id
        if viaf_uri:
            info["viaf_uri"] = viaf_uri
        return info

    # ── Authority matching functions ─────────────────────────────────
    def _match_against_authorities(
        self,
        name: str,
        mazal: MazalMatcher,
        viaf: VIAFMatcher | None,
    ) -> tuple[str | None, str | None]:
        """Match a name against Mazal and optionally VIAF authorities."""
        mazal_id = mazal.match_person(name)
        viaf_uri = viaf.match_person(name) if viaf else None
        return mazal_id, viaf_uri

    def _match_ner_entity(
        self,
        entity: dict,
        mazal: MazalMatcher,
        viaf: VIAFMatcher | None,
    ) -> dict[str, int]:
        """Match a single NER entity against authority databases."""
        # Guard clause: skip entities without valid names
        if not self._has_valid_name(entity, "person"):
            return {"counted": 0, "matched": 0}

        name = str(entity.get("person", "")).strip()
        mazal_id, viaf_uri = self._match_against_authorities(name, mazal, viaf)

        if mazal_id:
            entity["mazal_id"] = mazal_id
        if viaf_uri:
            entity["viaf_uri"] = viaf_uri

        return {"counted": 1, "matched": 1 if (mazal_id or viaf_uri) else 0}

    # ── Main processing loop ───────────────────────────────────────
    def run(self) -> None:
        # ... setup code ...
        for idx, record in enumerate(records):
            cn = str(record.get("_control_number", ""))

            # Process NER entities with flat structure
            ner_entities = record.get("entities") or []
            for entity in ner_entities:
                result = self._match_ner_entity(entity, mazal, viaf)
                total_entities += result["counted"]
                total_matched += result["matched"]

            # Process MARC persons
            marc_matches = self._match_marc_persons(cn, marc_by_cn, mazal, viaf)
            if marc_matches:
                record["marc_authority_matches"] = marc_matches
                total_entities += sum(m["counted"] for m in marc_matches)
                total_matched += sum(m["matched"] for m in marc_matches)

            # Process places
            place_result = self._match_marc_places(cn, marc_by_cn, kima)
            if place_result:
                record["kima_places"] = place_result
```

## Key Principles Applied

1. **Predicate functions** - Extract validation logic into pure functions that return booleans
2. **Guard clauses** - Return early when preconditions aren't met (no nesting)
3. **Transformation functions** - Extract data preparation into pure functions
4. **Separation of concerns** - Each function does one thing (match, create, validate)
5. **Flat structure** - Main loop is readable without deep nesting

Run this skill on specific files or ask the user which files need refactoring.
