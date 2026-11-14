"""
Guardrails for the Music Recommender.

validate_profile() checks a user profile before it reaches the scorer. It
either returns a cleaned profile plus a list of warnings, or raises
InvalidProfileError when the input cannot be used at all. Every decision is
logged so a bad run can be explained afterwards.
"""

import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ENERGY_RANGE = (0.0, 1.0)
TEMPO_RANGE = (40.0, 220.0)
DEFAULT_ENERGY = 0.5
DEFAULT_TEMPO = 100.0

LOW_CONFIDENCE_THRESHOLD = 0.45
HIGH_CONFIDENCE_THRESHOLD = 0.70


class InvalidProfileError(ValueError):
    """Raised when a profile is unusable (empty, wrong type, non-numeric...)."""


def _clean_label(value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidProfileError(f"genre and mood must be text, got {type(value).__name__}")
    value = value.strip().lower()
    return value or None


def _clean_number(name: str, value, low: float, high: float, default: float, warnings: List[str]) -> float:
    if value is None:
        warnings.append(f"{name} missing; using default {default}")
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidProfileError(f"{name} must be a number, got {value!r}")
    if math.isnan(value) or math.isinf(value):
        raise InvalidProfileError(f"{name} must be a finite number, got {value!r}")
    if value < low or value > high:
        clamped = min(max(value, low), high)
        warnings.append(f"{name} {value} is outside {low}-{high}; clamped to {clamped}")
        return float(clamped)
    return float(value)


def validate_profile(
    prefs: Optional[Dict],
    known_genres: Optional[set] = None,
    known_moods: Optional[set] = None,
) -> Tuple[Dict, List[str]]:
    """Return (clean_profile, warnings) or raise InvalidProfileError."""
    if not isinstance(prefs, dict) or not prefs:
        logger.error("Rejected profile: empty or not a dictionary (%r)", prefs)
        raise InvalidProfileError("profile is empty or not a dictionary")

    warnings: List[str] = []
    try:
        genre = _clean_label(prefs.get("favorite_genre"))
        mood = _clean_label(prefs.get("favorite_mood"))
        if genre is None and mood is None:
            raise InvalidProfileError("profile needs at least a favorite genre or a favorite mood")

        if genre is None:
            warnings.append("favorite_genre missing; ranking uses mood, energy and tempo only")
        elif known_genres is not None and genre not in known_genres:
            warnings.append(f"genre {genre!r} is not in the knowledge base; exact matches only, no related-genre credit")

        if mood is None:
            warnings.append("favorite_mood missing; ranking uses genre, energy and tempo only")
        elif known_moods is not None and mood not in known_moods:
            warnings.append(f"mood {mood!r} is not in the knowledge base; exact matches only, no related-mood credit")

        energy = _clean_number("target_energy", prefs.get("target_energy"), *ENERGY_RANGE, DEFAULT_ENERGY, warnings)
        tempo = _clean_number("target_tempo", prefs.get("target_tempo"), *TEMPO_RANGE, DEFAULT_TEMPO, warnings)
    except InvalidProfileError as exc:
        logger.error("Rejected profile %r: %s", prefs, exc)
        raise

    clean = {
        "favorite_genre": genre,
        "favorite_mood": mood,
        "target_energy": energy,
        "target_tempo": tempo,
    }
    for warning in warnings:
        logger.warning("Profile warning: %s", warning)
    logger.info("Accepted profile: %s", clean)
    return clean, warnings


def confidence_label(confidence: float) -> str:
    """Turn a 0-1 confidence value into high / medium / low."""
    if confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    if confidence >= LOW_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"
