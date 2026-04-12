"""Annotation module for adding scholarly annotations to converted manuscripts."""

from .annotation_importer import (
    AnnotationImporter,
    AnnotationType,
    CanonicalReferenceAnnotation,
    CertaintyAnnotation,
    ForeignUnitAnnotation,
    ScribalInterventionAnnotation,
    TextTraditionAnnotation,
    TextualRelationshipAnnotation,
    create_sample_annotation_files,
)

__all__ = [
    "AnnotationImporter",
    "AnnotationType",
    "CertaintyAnnotation",
    "TextTraditionAnnotation",
    "ScribalInterventionAnnotation",
    "CanonicalReferenceAnnotation",
    "TextualRelationshipAnnotation",
    "ForeignUnitAnnotation",
    "create_sample_annotation_files",
]
