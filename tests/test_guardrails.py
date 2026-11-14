import pytest

from src.guardrails import (
    InvalidProfileError,
    confidence_label,
    validate_profile,
)

KNOWN_GENRES = {"pop", "lofi"}
KNOWN_MOODS = {"happy", "chill"}


def good(**overrides):
    prefs = {
        "favorite_genre": "lofi", "favorite_mood": "chill",
        "target_energy": 0.4, "target_tempo": 78,
    }
    prefs.update(overrides)
    return prefs


def test_valid_profile_passes_with_no_warnings():
    clean, warnings = validate_profile(good(), KNOWN_GENRES, KNOWN_MOODS)
    assert warnings == []
    assert clean["favorite_genre"] == "lofi" and clean["target_energy"] == 0.4


def test_labels_are_trimmed_and_lowercased():
    clean, _ = validate_profile(good(favorite_genre="  LoFi ", favorite_mood="CHILL"), KNOWN_GENRES, KNOWN_MOODS)
    assert clean["favorite_genre"] == "lofi" and clean["favorite_mood"] == "chill"


@pytest.mark.parametrize("bad", [None, {}, [], "pop", 42])
def test_empty_or_wrong_type_profile_is_rejected(bad):
    with pytest.raises(InvalidProfileError):
        validate_profile(bad)


def test_profile_with_neither_genre_nor_mood_is_rejected():
    with pytest.raises(InvalidProfileError):
        validate_profile({"target_energy": 0.5, "target_tempo": 100})


@pytest.mark.parametrize("value", ["loud", True, float("nan"), float("inf"), [0.5]])
def test_non_numeric_energy_is_rejected(value):
    with pytest.raises(InvalidProfileError):
        validate_profile(good(target_energy=value))


def test_non_text_label_is_rejected():
    with pytest.raises(InvalidProfileError):
        validate_profile(good(favorite_genre=7))


def test_out_of_range_energy_is_clamped_with_warning():
    clean, warnings = validate_profile(good(target_energy=1.8), KNOWN_GENRES, KNOWN_MOODS)
    assert clean["target_energy"] == 1.0
    assert any("clamped" in w for w in warnings)


def test_negative_energy_is_clamped_to_zero():
    clean, _ = validate_profile(good(target_energy=-0.3))
    assert clean["target_energy"] == 0.0


def test_out_of_range_tempo_is_clamped_with_warning():
    clean, warnings = validate_profile(good(target_tempo=900))
    assert clean["target_tempo"] == 220.0
    assert any("target_tempo" in w for w in warnings)


def test_missing_numbers_get_defaults_with_warning():
    clean, warnings = validate_profile({"favorite_genre": "pop", "favorite_mood": "happy"})
    assert clean["target_energy"] == 0.5 and clean["target_tempo"] == 100.0
    assert len(warnings) == 2


def test_unknown_labels_warn_but_do_not_crash():
    clean, warnings = validate_profile(good(favorite_genre="polka", favorite_mood="grumpy"), KNOWN_GENRES, KNOWN_MOODS)
    assert clean["favorite_genre"] == "polka"
    assert sum("not in the knowledge base" in w for w in warnings) == 2


def test_missing_genre_alone_is_allowed_with_warning():
    clean, warnings = validate_profile(good(favorite_genre=None))
    assert clean["favorite_genre"] is None
    assert any("favorite_genre missing" in w for w in warnings)


def test_confidence_labels():
    assert confidence_label(0.95) == "high"
    assert confidence_label(0.55) == "medium"
    assert confidence_label(0.20) == "low"
