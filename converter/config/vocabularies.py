"""Controlled vocabularies and mappings for MARC to RDF conversion.

Fully aligned with Hebrew Manuscripts Ontology v1.4, including:
- Certainty levels for attributions
- Attribution sources (expert, catalog, AI, colophon)
- Epistemological status (fact vs interpretation)
- Data categories
- Interpretation methods
- Hierarchy types
- Paradigm types
- Text tradition features
"""

# =============================================================================
# CERTAINTY AND ATTRIBUTION (v1.4 Ontology Features)
# =============================================================================

CERTAINTY_LEVELS = {
    "certain": "Certain",
    "probable": "Probable",
    "possible": "Possible",
    "uncertain": "Uncertain",
    # Hebrew mappings
    "וודאי": "Certain",
    "סביר": "Probable",
    "אפשרי": "Possible",
    "לא וודאי": "Uncertain",
}

ATTRIBUTION_SOURCES = {
    "expert": "ExpertAttribution",
    "catalog": "CatalogAttribution",
    "colophon": "ColophonAttribution",
    "paleographic": "PaleographicAttribution",
    "ai": "AIAttribution",
    # Hebrew mappings
    "מומחה": "ExpertAttribution",
    "קטלוג": "CatalogAttribution",
    "קולופון": "ColophonAttribution",
    "פליאוגרפי": "PaleographicAttribution",
    "AI": "AIAttribution",
}

EPISTEMOLOGICAL_STATUSES = {
    "direct_observation": "DirectObservation",
    "scholarly_interpretation": "ScholarlyInterpretation",
    "computational_derivation": "ComputationalDerivation",
    "catalog_inherited": "CatalogInherited",
    "traditional_attribution": "TraditionalAttribution",
    # Hebrew mappings
    "תצפית ישירה": "DirectObservation",
    "פרשנות חוקר": "ScholarlyInterpretation",
    "גזירה חישובית": "ComputationalDerivation",
    "ירושה מקטלוג": "CatalogInherited",
    "ייחוס מסורתי": "TraditionalAttribution",
}

# Alias for backward compatibility
EPISTEMOLOGICAL_STATUS = EPISTEMOLOGICAL_STATUSES

DATA_CATEGORIES = {
    "physical_measurement": "PhysicalMeasurement",
    "colophon_content": "ColophonContent",
    "material_observation": "MaterialObservation",
    "date_attribution": "DateAttribution",
    "authorship_attribution": "AuthorshipAttribution",
    "scribe_identification": "ScribeIdentification",
    "place_attribution": "PlaceAttribution",
    "work_identification": "WorkIdentification",
    "textual_relationship": "TextualRelationship",
    # Hebrew mappings
    "מדידה פיזית": "PhysicalMeasurement",
    "תוכן קולופון": "ColophonContent",
    "תצפית חומר": "MaterialObservation",
    "ייחוס תאריך": "DateAttribution",
    "ייחוס מחברות": "AuthorshipAttribution",
    "זיהוי סופר": "ScribeIdentification",
    "ייחוס מקום": "PlaceAttribution",
    "זיהוי יצירה": "WorkIdentification",
    "יחס טקסטואלי": "TextualRelationship",
}

INTERPRETATION_METHODS = {
    "paleographic": "PaleographicAnalysis",
    "codicological": "CodicologicalAnalysis",
    "linguistic": "LinguisticAnalysis",
    "historical": "HistoricalContextAnalysis",
    "comparative": "ComparativeTextualAnalysis",
    "ai": "AIBasedAnalysis",
    "radiocarbon": "RadiocarbonDating",
    "watermark": "WatermarkAnalysis",
    # Hebrew mappings
    "ניתוח פליאוגרפי": "PaleographicAnalysis",
    "ניתוח קודיקולוגי": "CodicologicalAnalysis",
    "ניתוח לשוני": "LinguisticAnalysis",
    "ניתוח הקשר היסטורי": "HistoricalContextAnalysis",
    "ניתוח טקסטואלי השוואתי": "ComparativeTextualAnalysis",
    "ניתוח מבוסס AI": "AIBasedAnalysis",
    "תיארוך פחמן-14": "RadiocarbonDating",
    "ניתוח סימני מים": "WatermarkAnalysis",
}

# Mapping of data types to their default epistemological status
# True = factual (DirectObservation), False = interpretive (ScholarlyInterpretation)
DATA_FACTUALITY = {
    "extent": True,  # Counting folios is factual
    "dimensions": True,  # Physical measurement
    "material": True,  # Observable
    "colophon_text": True,  # Direct transcription
    "title": True,  # From manuscript
    "date": False,  # Usually interpretive
    "place": False,  # Usually interpretive
    "author": False,  # Attribution required
    "scribe": False,  # Attribution required
    "script_type": False,  # Paleographic interpretation
    "work_identification": False,  # Scholarly judgment
}

# =============================================================================
# LANGUAGE CODES
# =============================================================================

LANGUAGE_CODES = {
    "heb": "Hebrew",
    "ara": "Arabic",
    "arc": "Aramaic",
    "jrb": "JudeoArabic",
    "lad": "Ladino",
    "yid": "Yiddish",
    "jpr": "JudeoPersian",
    "lat": "Latin",
    "grc": "Greek",
    "per": "Persian",
    "ger": "German",
    "spa": "Spanish",
    "ita": "Italian",
    "fre": "French",
    "eng": "English",
    "por": "Portuguese",
    "tur": "Turkish",
    "und": "Undetermined",
    "mul": "Multiple",
}

MATERIAL_TYPES = {
    "parchment": "Parchment",
    "vellum": "Parchment",
    "paper": "Paper",
    "papyrus": "Papyrus",
    "קלף": "Parchment",
    "נייר": "Paper",
    "פפירוס": "Papyrus",
}

SCRIPT_TYPES = {
    "ashkenazic": "AshkenaziScript",
    "ashkenazi": "AshkenaziScript",
    "אשכנזי": "AshkenaziScript",
    "אשכנזית": "AshkenaziScript",
    "sephardic": "SepharadicScript",
    "sefardic": "SepharadicScript",
    "ספרדי": "SepharadicScript",
    "ספרדית": "SepharadicScript",
    "italian": "ItalianScript",
    "איטלקי": "ItalianScript",
    "איטלקית": "ItalianScript",
    "byzantine": "ByzantineScript",
    "ביזנטי": "ByzantineScript",
    "ביזנטית": "ByzantineScript",
    "yemenite": "YemeniteScript",
    "תימני": "YemeniteScript",
    "תימנית": "YemeniteScript",
    "oriental": "OrientalScript",
    "מזרחי": "OrientalScript",
    "מזרחית": "OrientalScript",
    "persian": "PersianScript",
    "פרסי": "PersianScript",
    "פרסית": "PersianScript",
}

SCRIPT_MODES = {
    "square": "SquareScript",
    "מרובע": "SquareScript",
    "מרובעת": "SquareScript",
    "semi-cursive": "SemicursiveScript",
    "semicursive": "SemicursiveScript",
    "בינוני": "SemicursiveScript",
    "בינונית": "SemicursiveScript",
    "cursive": "CursiveScript",
    "רהוט": "CursiveScript",
    "רהוטה": "CursiveScript",
}

ROLE_MAPPINGS = {
    "author": "author",
    "מחבר": "author",
    "scribe": "scribe",
    "סופר": "scribe",
    "copyist": "scribe",
    "מעתיק": "scribe",
    "illuminator": "illuminator",
    "מאייר": "illuminator",
    "commentator": "commentator",
    "פרשן": "commentator",
    "מפרש": "commentator",
    "translator": "translator",
    "מתרגם": "translator",
    "former owner": "former_owner",
    "בעלים קודם": "former_owner",
    "editor": "editor",
    "עורך": "editor",
    "compiler": "compiler",
    "מלקט": "compiler",
}

DATE_TYPE_CODES = {
    "s": "single",
    "q": "questionable",
    "m": "multiple",
    "n": "unknown",
    "c": "continuing",
    "d": "ceased",
    "e": "detailed",
    "i": "inclusive",
    "k": "bulk",
    "p": "distribution",
    "r": "reprint",
    "t": "publication_copyright",
    "u": "continuing_unknown",
}

# =============================================================================
# DATE FORMAT TYPES (v1.4 - Structured Date Classification)
# =============================================================================

DATE_FORMAT_TYPES = {
    "gregorian_year": "GregorianYear",
    "hebrew_year": "HebrewYear",
    "full_date": "FullDate",
    "unstructured": "UnstructuredDate",
    # Hebrew mappings
    "שנה גרגוריאנית": "GregorianYear",
    "שנה עברית": "HebrewYear",
    "תאריך מלא": "FullDate",
    "טקסט חופשי": "UnstructuredDate",
}

GENRE_MAPPINGS = {
    "bible": "BiblicalText",
    'תנ"ך': "BiblicalText",
    "מקרא": "BiblicalText",
    "mishnah": "MishnaicText",
    "משנה": "MishnaicText",
    "talmud": "TalmudicText",
    "תלמוד": "TalmudicText",
    "halakha": "HalachicText",
    "הלכה": "HalachicText",
    "kabbalah": "KabbalisticText",
    "קבלה": "KabbalisticText",
    "philosophy": "PhilosophicalText",
    "פילוסופיה": "PhilosophicalText",
    "poetry": "PoeticText",
    "שירה": "PoeticText",
    "liturgy": "LiturgicalText",
    "ליטורגיה": "LiturgicalText",
    "תפילה": "LiturgicalText",
    "medicine": "MedicalText",
    "רפואה": "MedicalText",
    "astronomy": "AstronomicalText",
    "אסטרונומיה": "AstronomicalText",
    "grammar": "GrammaticalText",
    "דקדוק": "GrammaticalText",
    "commentary": "CommentaryText",
    "פירוש": "CommentaryText",
}

# =============================================================================
# CANONICAL HIERARCHIES (v1.3+ Ontology Features)
# =============================================================================

CANONICAL_HIERARCHIES = {
    "bible": "Bible_hierarchy",
    'תנ"ך': "Bible_hierarchy",
    "מקרא": "Bible_hierarchy",
    "mishnah": "Mishnah_hierarchy",
    "משנה": "Mishnah_hierarchy",
    "talmud_bavli": "Talmud_Bavli_hierarchy",
    "תלמוד בבלי": "Talmud_Bavli_hierarchy",
    "בבלי": "Talmud_Bavli_hierarchy",
    "talmud_yerushalmi": "Talmud_Yerushalmi_hierarchy",
    "תלמוד ירושלמי": "Talmud_Yerushalmi_hierarchy",
    "ירושלמי": "Talmud_Yerushalmi_hierarchy",
    "mishneh_torah": "Mishneh_Torah_hierarchy",
    "משנה תורה": "Mishneh_Torah_hierarchy",
    'רמב"ם': "Mishneh_Torah_hierarchy",
    "shulchan_aruch": "Shulchan_Aruch_hierarchy",
    "שולחן ערוך": "Shulchan_Aruch_hierarchy",
    "zohar": "Zohar_hierarchy",
    "זוהר": "Zohar_hierarchy",
    "midrash": "Midrash_hierarchy",
    "מדרש": "Midrash_hierarchy",
}

# =============================================================================
# CONDITION TYPES
# =============================================================================

CONDITION_TYPES = {
    "excellent": "Excellent",
    "מצוין": "Excellent",
    "good": "Good",
    "טוב": "Good",
    "fair": "Fair",
    "סביר": "Fair",
    "poor": "Poor",
    "גרוע": "Poor",
    "fragmentary": "Fragmentary",
    "חלקי": "Fragmentary",
    "קטעי": "Fragmentary",
}

# =============================================================================
# UNIT STATUS (for foreign/core unit distinction)
# =============================================================================

UNIT_STATUS_TYPES = {
    "core": "CoreUnit_status",
    "later_addition": "LaterAddition_status",
    "binder_addition": "BinderAddition_status",
    "unrelated_fragment": "UnrelatedFragment_status",
    "protective_leaf": "ProtectiveLeaf_status",
    # Hebrew mappings
    "יחידת ליבה": "CoreUnit_status",
    "תוספת מאוחרת": "LaterAddition_status",
    "תוספת כורך": "BinderAddition_status",
    "שבר לא קשור": "UnrelatedFragment_status",
    "דף מגן": "ProtectiveLeaf_status",
}

# =============================================================================
# HIERARCHY TYPES (v1.4 - Nested CU Support)
# =============================================================================

HIERARCHY_TYPES = {
    "simple": "SimpleHierarchy",
    "nested": "NestedHierarchy",
    "complex": "ComplexHierarchy",
    "fragmentary": "FragmentaryHierarchy",
    # Hebrew mappings
    "היררכיה פשוטה": "SimpleHierarchy",
    "היררכיה מקוננת": "NestedHierarchy",
    "היררכיה מורכבת": "ComplexHierarchy",
    "היררכיה קטעית": "FragmentaryHierarchy",
}

# =============================================================================
# PARADIGM TYPES (v1.4 - Dual Paradigm Support)
# =============================================================================

PARADIGM_TYPES = {
    "bibliographic": "BibliographicParadigm",
    "philological": "PhilologicalParadigm",
    "hybrid": "HybridParadigm",
    # Hebrew mappings
    "פרדיגמה ביבליוגרפית": "BibliographicParadigm",
    "פרדיגמה פילולוגית": "PhilologicalParadigm",
    "פרדיגמה היברידית": "HybridParadigm",
}

# =============================================================================
# SCRIBAL INTERVENTION TYPES (v1.4)
# =============================================================================

INTERVENTION_TYPES = {
    "correction": "Correction_type",
    "erasure": "Erasure_type",
    "interlinear_addition": "Interlinear_addition_type",
    "marginal_gloss": "Marginal_gloss_type",
    "later_hand": "Later_hand_type",
    # Hebrew mappings
    "תיקון": "Correction_type",
    "מחיקה": "Erasure_type",
    "הוספה בין השורות": "Interlinear_addition_type",
    "ביאור בשוליים": "Marginal_gloss_type",
    "יד מאוחרת": "Later_hand_type",
}

# =============================================================================
# VARIANT SIGNIFICANCE TYPES (v1.4 - Text Tradition)
# =============================================================================

VARIANT_SIGNIFICANCE = {
    "orthographic": "Orthographic_variant",
    "lexical": "Lexical_variant",
    "syntactic": "Syntactic_variant",
    "semantic": "Semantic_variant",
    "addition": "Addition_variant",
    "omission": "Omission_variant",
    # Hebrew mappings
    "גרסה כתיבית": "Orthographic_variant",
    "גרסה לקסיקלית": "Lexical_variant",
    "גרסה תחבירית": "Syntactic_variant",
    "גרסה משמעותית": "Semantic_variant",
    "הוספה": "Addition_variant",
    "השמטה": "Omission_variant",
}

# =============================================================================
# VOCALIZATION TYPES
# =============================================================================

VOCALIZATION_TYPES = {
    "tiberian": "TiberianVocalization",
    "טברייני": "TiberianVocalization",
    "babylonian": "BabylonianVocalization",
    "בבלי": "BabylonianVocalization",
    "palestinian": "PalestinianVocalization",
    "ארץ-ישראלי": "PalestinianVocalization",
    "none": "NoVocalization",
    "ללא ניקוד": "NoVocalization",
}

# =============================================================================
# BINDING TYPES (v1.4 - From Ontology)
# =============================================================================

BINDING_TYPES = {
    "original": "OriginalBinding",
    "מקורי": "OriginalBinding",
    "כריכה מקורית": "OriginalBinding",
    "rebinding": "Rebinding",
    "כריכה מחדש": "Rebinding",
    "כריכה חדשה": "Rebinding",
    "conservation": "ConservationBinding",
    "כריכת שימור": "ConservationBinding",
    "unbound": "Unbound",
    "ללא כריכה": "Unbound",
    "לא כרוך": "Unbound",
}

# =============================================================================
# DECORATION TYPES (v1.4 - From Ontology)
# =============================================================================

DECORATION_TYPES = {
    "full_page_illumination": "FullPageIllumination",
    "איור עמוד מלא": "FullPageIllumination",
    "initial_letters": "InitialLetters",
    "אותיות ראשיות מעוטרות": "InitialLetters",
    "אותיות ראשיות": "InitialLetters",
    "marginal_decoration": "MarginalDecoration",
    "עיטור שוליים": "MarginalDecoration",
    "carpet_page": "CarpetPage",
    "עמוד שטיח": "CarpetPage",
    "micrography": "Micrography",
    "מיקרוגרפיה": "Micrography",
    "כתב זעיר": "Micrography",
}

# =============================================================================
# TEXT TYPES (v1.4 - From Ontology)
# =============================================================================

TEXT_TYPES = {
    "main_text": "MainText",
    "טקסט עיקרי": "MainText",
    "additional": "AdditionalText",
    "additional_text": "AdditionalText",
    "טקסט נוסף": "AdditionalText",
    "נוסף": "AdditionalText",
    "marginal": "MarginalText",
    "marginal_text": "MarginalText",
    "טקסט בשוליים": "MarginalText",
    "שוליים": "MarginalText",
    "commentary": "CommentaryText",
    "פירוש": "CommentaryText",
    "supercommentary": "SupercommentaryText",
    "פירוש על פירוש": "SupercommentaryText",
    "gloss": "GlossText",
    "ביאור": "GlossText",
    "responsum": "ResponsumText",
    'שו"ת': "ResponsumText",
    "תשובה": "ResponsumText",
}
