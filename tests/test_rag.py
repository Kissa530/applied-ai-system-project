import pytest

from src.recommender import load_songs, recommend_songs, recommend_songs_rag
from src.retriever import KnowledgeBase, KnowledgeBaseError

SONGS = load_songs("data/songs.csv")
KB = KnowledgeBase.load()

CHILL_LOFI = {
    "favorite_genre": "lofi", "favorite_mood": "chill",
    "target_energy": 0.40, "target_tempo": 78,
}
HIGH_ENERGY_POP = {
    "favorite_genre": "pop", "favorite_mood": "happy",
    "target_energy": 0.90, "target_tempo": 125,
}
CONFLICTING = {
    "favorite_genre": "classical", "favorite_mood": "melancholy",
    "target_energy": 0.90, "target_tempo": 140,
}


def titles_rag(prefs, k=5):
    return [r.song["title"] for r in recommend_songs_rag(prefs, SONGS, KB, k=k)]


def titles_base(prefs, k=5):
    return [song["title"] for song, _, _ in recommend_songs(prefs, SONGS, k=k)]


# ---- retrieval -----------------------------------------------------------

def test_retrieve_finds_entries_for_known_labels():
    context = KB.retrieve("lofi", "chill")
    assert context.found_anything
    assert context.mood_related["relaxed"] > 0
    assert context.genre_related["ambient"] > 0


def test_retrieve_misses_unknown_labels():
    context = KB.retrieve("polka", "grumpy")
    assert not context.found_anything
    assert context.mood_related == {} and context.genre_related == {}


def test_every_catalog_label_exists_in_the_knowledge_base():
    assert {s["genre"] for s in SONGS} <= set(KB.genres)
    assert {s["mood"] for s in SONGS} <= set(KB.moods)


def test_related_labels_all_exist_and_weights_are_below_one():
    for table in (KB.moods, KB.genres):
        for name, entry in table.items():
            for related, weight in entry["related"].items():
                assert related in table, f"{name} -> {related} is not a known label"
                assert 0 < weight < 1, f"{name} -> {related} weight {weight} must be below an exact match"


def test_missing_knowledge_base_raises_clear_error(tmp_path):
    with pytest.raises(KnowledgeBaseError):
        KnowledgeBase.load(str(tmp_path / "nope.json"))


def test_malformed_knowledge_base_raises_clear_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(KnowledgeBaseError):
        KnowledgeBase.load(str(bad))


# ---- retrieval changes behaviour (the point of RAG) ----------------------

def test_retrieval_changes_the_ranking_for_chill_lofi():
    base, rag = titles_base(CHILL_LOFI), titles_rag(CHILL_LOFI)
    assert base != rag
    # 'relaxed' jazz was invisible to the baseline and is pulled in by retrieved weights.
    assert "Coffee Shop Stories" not in base
    assert "Coffee Shop Stories" in rag


def test_related_mood_earns_partial_credit_only():
    rec = next(r for r in recommend_songs_rag(CHILL_LOFI, SONGS, KB, k=5) if r.song["title"] == "Focus Flow")
    related = [reason for reason in rec.reasons if reason.startswith("related mood")]
    assert related and "+0.50" in related[0]  # 1.0 mood points x 0.5 retrieved weight


def test_exact_match_always_beats_related_match():
    ranked = recommend_songs_rag(CHILL_LOFI, SONGS, KB, k=18)
    exact = next(r for r in ranked if r.song["title"] == "Midnight Coding")  # genre + mood exact
    related = next(r for r in ranked if r.song["title"] == "Focus Flow")     # genre exact, mood related
    assert exact.score > related.score


def test_rooftop_lights_now_outranks_gym_hero_for_happy_pop():
    # The original model card flagged a workout song (Gym Hero) landing in a "happy" playlist.
    rag = titles_rag(HIGH_ENERGY_POP)
    assert rag.index("Rooftop Lights") < rag.index("Gym Hero")


# ---- confidence and flags --------------------------------------------------

def test_confidence_is_between_zero_and_one_for_every_result():
    for prefs in (CHILL_LOFI, HIGH_ENERGY_POP, CONFLICTING):
        for rec in recommend_songs_rag(prefs, SONGS, KB, k=18):
            assert 0.0 <= rec.confidence <= 1.0


def test_perfect_match_has_high_confidence():
    top = recommend_songs_rag(CHILL_LOFI, SONGS, KB, k=1)[0]
    assert top.confidence >= 0.95 and top.confidence_label == "high"


def test_poor_matches_are_flagged_low_confidence():
    ranked = recommend_songs_rag(HIGH_ENERGY_POP, SONGS, KB, k=5)
    assert ranked[-1].confidence_label == "low"
    assert any("low confidence" in flag for flag in ranked[-1].flags)


def test_energy_gap_is_flagged_for_the_conflicting_profile():
    top = recommend_songs_rag(CONFLICTING, SONGS, KB, k=1)[0]
    assert top.song["title"] == "Winter Window"  # known limitation, documented in the model card
    assert any("energy gap" in flag for flag in top.flags)


def test_results_are_deterministic():
    assert titles_rag(HIGH_ENERGY_POP) == titles_rag(HIGH_ENERGY_POP)


# ---- free-text retrieval ----------------------------------------------------

def test_free_text_calm_study_maps_to_chill():
    profile = KB.profile_from_text("something calm to study to")
    assert profile["favorite_mood"] == "chill"
    assert profile["target_energy"] == KB.get_mood("chill")["typical_energy"]


def test_free_text_workout_maps_to_intense_without_inventing_a_genre():
    profile = KB.profile_from_text("loud gym workout music")
    assert profile["favorite_mood"] == "intense"
    assert profile["favorite_genre"] is None  # 'loud' alone must not select metal


def test_free_text_names_a_genre_only_when_it_is_named():
    assert KB.profile_from_text("some jazz for dinner")["favorite_genre"] == "jazz"
    assert KB.profile_from_text("indie pop to dance to")["favorite_genre"] == "indie pop"


def test_free_text_gibberish_or_empty_returns_none():
    assert KB.profile_from_text("zzzz qqqq") is None
    assert KB.profile_from_text("   ") is None
    assert KB.profile_from_text("") is None
