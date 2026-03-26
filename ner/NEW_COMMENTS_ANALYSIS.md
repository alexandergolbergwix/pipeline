# New Comments Analysis (PDF v3)

## Summary
Found **30 comments** in the new PDF. After comparing with current paper, here's the status:

---

## ✅ ALREADY FIXED (11 comments)

| # | Page | Comment | Status |
|---|------|---------|--------|
| 4 | 1 | LOD needs explanation first | ✅ Abstract now starts with LOD explanation |
| 7 | 1 | "Elmalich" → "Elmalech" | ✅ Fixed in paper |
| 13 | 2 | Compare to Ben-Gigi's work | ✅ Added Section 2.4 with Ben-Gigi comparison |
| 14 | 2 | YU repository link | ✅ Verified as Bruce 2021, already cited |
| 19 | 4 | Missing multi-entity literature | ✅ Added Yang et al. [2022], Zhao et al. [2024] |
| 23 | 6 | Too many Maayan citations | ✅ Diversified citations |
| 26 | 6 | Need concrete example + method overview | ✅ Added Section 3.1 (concrete example) and Section 3.2 (method overview) |
| 3 | 1 | Comparison to other methods? | ✅ Added three-way baseline comparison |
| - | - | DictaBERT F1 scores | ✅ Empirically verified and updated |
| - | - | 100% accuracy clarification | ✅ Now specifies "single-entity samples" |

---

## ⚠️ STILL RELEVANT - NEED TO FIX (14 comments)

### Style Issues (GPT-like language)

| # | Page | Issue | Current Text | Suggested Fix |
|---|------|-------|--------------|---------------|
| 5 | 1 | "democratizes" sounds AI-generated | Appears 4 times in paper | Replace with more natural language |
| 6 | 1 | GPT style em-dashes "—" | Still present throughout | Reduce or rephrase |
| 11 | 2 | "—critically—" GPT style | Not found (may be fixed) | ✓ |

### Clarity Issues

| # | Page | Comment (Hebrew) | Issue |
|---|------|------------------|-------|
| 1 | 1 | "לא מובנת לי הכוונה" | "annotation bottleneck" unclear |
| 2 | 1 | "לא הבנתי את הקשר למילה טבעי" | Word "naturally" connection unclear |
| 8 | 1 | "יותר מידי entity במשפט" | Too much "entity" in sentences |
| 12 | 2 | "multi-entity person extraction" לא מובן | Term needs clearer explanation |
| 18 | 3 | "לא ברור לי הקשר בין המחקרים" | Connection between researches unclear (Broader Impact section) |
| 20 | 5 | "לא הצלחתי להבין איזה מסר" | Unclear paragraph message |
| 21 | 5 | Knowledge graphs statement unclear | "trace manuscript circulation" unclear |
| 24 | 6 | "כתוב בצורה מאוד מעורפלת" | Vague writing |

### Content Issues

| # | Page | Comment | Issue |
|---|------|---------|-------|
| 9 | 1 | "אין צורך להתייחס לבית אריה" | Beit-Arie reference trivial/unnecessary |
| 10 | 1 | "לא ברור לאיזה קטלוגים" | Should specify National Library catalog |
| 22 | 5 | "critical cataloging studies" | Unknown term - needs explanation or removal |
| 27 | 6 | "values" term not appearing | Citation mentions term not in paper |

### Structure Issues

| # | Page | Comment | Issue |
|---|------|---------|-------|
| 28 | 8 | "צריך כל כך הרבה תתי סעיפים?" | Too many subsections? |
| 30 | 17 | Conclusion should be Section 7 | Current section numbering off |

---

## ❓ NEED CLARIFICATION (5 comments)

| # | Page | Comment | Notes |
|---|------|---------|-------|
| 15 | 2 | "השם גם צריך להיות בתוך הסוגריים" | Name inside parentheses - unclear which |
| 16 | 2 | "המילים מחולקות" | Words look divided - may be PDF rendering |
| 17 | 2 | "יש להוריד את ההדגשות" | Remove highlights - need to check what |
| 25 | 6 | "איך כבר פה מדובר על ביצועים?" | Why performance mentioned early? |
| 29 | 10 | "Gold:" לא הבנתי | "Gold:" unclear - check context |

---

## Recommended Actions

### High Priority (Style)
1. **Remove/replace "democratizes"** (4 occurrences) - sounds AI-generated
2. **Review em-dash usage** - reduce GPT-style "—" patterns

### High Priority (Content)
3. **Clarify "annotation bottleneck"** - make meaning clearer
4. **Define "multi-entity extraction"** more clearly early in paper
5. **Remove Beit-Arie citation** or justify why it's needed
6. **Specify "National Library of Israel catalogs"** instead of just "catalogs"
7. **Explain or remove "critical cataloging studies"** term

### Medium Priority (Structure)
8. **Review Broader Impact section** - connection to main research unclear
9. **Consider consolidating subsections** (Section 3 has 6+ subsections)
10. **Check section numbering** for conclusion placement

### Low Priority
11. Review formatting issues (parentheses, word divisions)
12. Check "values" citation accuracy

