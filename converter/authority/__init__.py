"""Mazal (NLI) Authority file integration for entity URI resolution.

This package provides integration with the Israeli National Library's
Mazal authority files, enabling the converter to use official NLI
identifiers instead of locally-generated URIs.

Mazal is the Israeli national authority file containing ~4.5 million
authority records with multilingual support (Hebrew, Latin, Arabic, Cyrillic)
and links to international authority systems (VIAF, ORCID, Wikidata).
"""

from .kima_index import KimaIndex, build_kima_index
from .kima_matcher import KimaMatcher
from .mazal_index import MazalIndex, build_index
from .mazal_matcher import MazalMatcher
from .viaf_matcher import VIAFMatcher

__all__ = [
    "MazalMatcher",
    "MazalIndex",
    "build_index",
    "VIAFMatcher",
    "KimaMatcher",
    "KimaIndex",
    "build_kima_index",
]
