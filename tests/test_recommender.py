from src.recommender import (
    MAX_SCORE,
    Recommender,
    Song,
    UserProfile,
    load_songs,
    recommend_songs,
    recommend_songs_rag,
    score_song,
)
from src.retriever import KnowledgeBase


def make_small_recommender(knowledge_base=None) -> Recommender:
    songs = [
        Song(
            id=1,
            title="Test Pop Track",
            artist="Test Artist",
            genre="pop",
            mood="happy",
            energy=0.8,
            tempo_bpm=120,
            valence=0.9,
            danceability=0.8,
            acousticness=0.2,
        ),
        Song(
            id=2,
            title="Chill Lofi Loop",
            artist="Test Artist",
            genre="lofi",
            mood="chill",
            energy=0.4,
            tempo_bpm=80,
            valence=0.6,
            danceability=0.5,
            acousticness=0.9,
        ),
    ]
    return Recommender(songs, knowledge_base)


def pop_user() -> UserProfile:
    return UserProfile(
        favorite_genre="pop",
        favorite_mood="happy",
        target_energy=0.8,
        likes_acoustic=False,
        target_tempo=120,
    )


def lofi_user() -> UserProfile:
    return UserProfile(
        favorite_genre="lofi",
        favorite_mood="chill",
        target_energy=0.4,
        likes_acoustic=True,
        target_tempo=80,
    )


def test_recommend_returns_songs_sorted_by_score():
    results = make_small_recommender().recommend(pop_user(), k=2)

    assert len(results) == 2
    assert results[0].genre == "pop"
    assert results[0].mood == "happy"


def test_recommend_actually_ranks_by_preference_not_list_order():
    # The lofi song is listed second, so a stub that returns songs[:k] would fail this.
    results = make_small_recommender().recommend(lofi_user(), k=2)

    assert results[0].title == "Chill Lofi Loop"
    assert results[1].title == "Test Pop Track"


def test_recommend_respects_k():
    assert len(make_small_recommender().recommend(pop_user(), k=1)) == 1


def test_recommend_works_with_knowledge_base_too():
    kb = KnowledgeBase.load()
    results = make_small_recommender(kb).recommend(lofi_user(), k=2)
    assert results[0].title == "Chill Lofi Loop"


def test_explain_recommendation_returns_non_empty_string():
    rec = make_small_recommender()
    explanation = rec.explain_recommendation(pop_user(), rec.songs[0])

    assert isinstance(explanation, str)
    assert explanation.strip() != ""


def test_explanation_without_kb_lists_the_real_score_reasons():
    rec = make_small_recommender()
    explanation = rec.explain_recommendation(pop_user(), rec.songs[0])

    assert "genre match" in explanation
    assert "mood match" in explanation


def test_explanation_with_kb_uses_retrieved_description():
    kb = KnowledgeBase.load()
    rec = make_small_recommender(kb)
    explanation = rec.explain_recommendation(pop_user(), rec.songs[0])

    # The retrieved mood description must be part of the explanation text.
    assert kb.get_mood("happy")["description"] in explanation


def test_baseline_scoring_matches_original_worked_example():
    # README worked example: Library Rain for the lofi/chill profile totals about 4.41.
    songs = load_songs("data/songs.csv")
    library_rain = next(s for s in songs if s["title"] == "Library Rain")
    prefs = {
        "favorite_genre": "lofi", "favorite_mood": "chill",
        "target_energy": 0.40, "target_tempo": 78,
    }
    score, _ = score_song(prefs, library_rain)
    assert round(score, 2) == 4.42


def test_baseline_and_rag_agree_when_nothing_is_related():
    # An unknown genre and mood retrieve nothing, so RAG scoring equals baseline scoring.
    songs = load_songs("data/songs.csv")
    kb = KnowledgeBase.load()
    prefs = {
        "favorite_genre": "polka", "favorite_mood": "grumpy",
        "target_energy": 0.5, "target_tempo": 100,
    }
    base = [(s["title"], round(score, 2)) for s, score, _ in recommend_songs(prefs, songs, k=5)]
    rag = [(r.song["title"], r.score) for r in recommend_songs_rag(prefs, songs, kb, k=5)]
    assert base == rag


def test_max_score_is_four_and_a_half():
    assert MAX_SCORE == 4.5
