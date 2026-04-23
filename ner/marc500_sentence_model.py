"""MARC 500 sentence classification model — re-exports GenreClassificationModel.

Exists as a separate file so build_app.sh can bundle it without the full
training script, while keeping the architecture in one place.
"""

from genre_classifier_model import GenreClassificationModel

__all__ = ["GenreClassificationModel"]
