import csv
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.guardrails import confidence_label
from src.retriever import DEFAULT_TEMPO, KnowledgeBase, RetrievalContext

logger = logging.getLogger(__name__)

GENRE_MATCH_POINTS = 2.0
MOOD_MATCH_POINTS = 1.0
ENERGY_CLOSENESS_MULTIPLIER = 1.0
TEMPO_CLOSENESS_MAX_POINTS = 0.5
TEMPO_CLOSENESS_RANGE_BPM = 94
ENERGY_GAP_WARNING = 0.5  # flag results whose energy is this far from the target

# The highest score a song can earn (an exact genre and mood match, with
# energy and tempo exactly on target). Related-label credit is always less
# than an exact match, so this stays the ceiling.
MAX_SCORE = (
    GENRE_MATCH_POINTS
    + MOOD_MATCH_POINTS
    + ENERGY_CLOSENESS_MULTIPLIER
    + TEMPO_CLOSENESS_MAX_POINTS
)


@dataclass
class Song:
    """Represents a song and its attributes."""
    id: int
    title: str
    artist: str
    genre: str
    mood: str
    energy: float
    tempo_bpm: float
    valence: float
    danceability: float
    acousticness: float


@dataclass
class UserProfile:
    """Represents a user's taste preferences."""
    favorite_genre: str
    favorite_mood: str
    target_energy: float
    likes_acoustic: bool
    target_tempo: Optional[float] = None


@dataclass
class Recommendation:
    """One ranked result from the retrieval-aware recommender."""
    song: Dict
    score: float
    confidence: float
    confidence_label: str
    reasons: List[str]
    explanation: str
    flags: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Original content-based scoring (VibeFinder 1.0). Kept unchanged: it is the
# baseline that the retrieval-aware scoring below is compared against.
# ---------------------------------------------------------------------------

def load_songs(csv_path: str) -> List[Dict]:
    """Load songs from a CSV file."""
    with open(csv_path, newline="", encoding="utf-8") as csv_file:
        songs = list(csv.DictReader(csv_file))

    for song in songs:
        song["id"] = int(song["id"])
        for field_name in ("energy", "tempo_bpm", "valence", "danceability", "acousticness"):
            song[field_name] = float(song[field_name])

    return songs


def score_song(user_prefs: Dict, song: Dict) -> Tuple[float, List[str]]:
    """Score one song against a user's preferences."""
    score = 0.0
    reasons = []

    if song["genre"] == user_prefs["favorite_genre"]:
        score += GENRE_MATCH_POINTS
        reasons.append(f"genre match (+{GENRE_MATCH_POINTS:.1f})")

    if song["mood"] == user_prefs["favorite_mood"]:
        score += MOOD_MATCH_POINTS
        reasons.append(f"mood match (+{MOOD_MATCH_POINTS:.1f})")

    energy_score = ENERGY_CLOSENESS_MULTIPLIER * max(
        0,
        1 - abs(song["energy"] - user_prefs["target_energy"]),
    )
    score += energy_score
    reasons.append(f"energy closeness (+{energy_score:.2f})")

    tempo_score = TEMPO_CLOSENESS_MAX_POINTS * max(
        0,
        1 - abs(song["tempo_bpm"] - user_prefs["target_tempo"]) / TEMPO_CLOSENESS_RANGE_BPM,
    )
    score += tempo_score
    reasons.append(f"tempo closeness (+{tempo_score:.2f})")

    return score, reasons


def recommend_songs(user_prefs: Dict, songs: List[Dict], k: int = 5) -> List[Tuple[Dict, float, str]]:
    """Rank songs by how well they match the user's preferences (baseline)."""
    scored_songs = []
    for song in songs:
        score, reasons = score_song(user_prefs, song)
        scored_songs.append((song, score, ", ".join(reasons)))

    return sorted(scored_songs, key=lambda result: result[1], reverse=True)[:k]


# ---------------------------------------------------------------------------
# Retrieval-augmented scoring.
# ---------------------------------------------------------------------------

def score_song_rag(user_prefs: Dict, song: Dict, context: RetrievalContext) -> Tuple[float, List[str]]:
    """Baseline score plus partial credit from retrieved related genres/moods.

    A song whose genre or mood is not an exact match but is listed as related
    in the retrieved knowledge base entry earns a share of the exact-match
    points, scaled by the retrieved weight. This is what lets "relaxed" count
    as close to "chill" instead of a total miss.
    """
    score, reasons = score_song(user_prefs, song)

    if song["genre"] != user_prefs["favorite_genre"]:
        weight = context.genre_related.get(song["genre"], 0.0)
        if weight > 0:
            credit = GENRE_MATCH_POINTS * weight
            score += credit
            reasons.append(
                f"related genre: {song['genre']} ~ {user_prefs['favorite_genre']} "
                f"(+{credit:.2f}, retrieved weight {weight:.1f})"
            )

    if song["mood"] != user_prefs["favorite_mood"]:
        weight = context.mood_related.get(song["mood"], 0.0)
        if weight > 0:
            credit = MOOD_MATCH_POINTS * weight
            score += credit
            reasons.append(
                f"related mood: {song['mood']} ~ {user_prefs['favorite_mood']} "
                f"(+{credit:.2f}, retrieved weight {weight:.1f})"
            )

    return score, reasons


def build_explanation(user_prefs: Dict, song: Dict, context: RetrievalContext) -> str:
    """Write a plain-language explanation using the retrieved descriptions."""
    parts = []
    fav_mood = user_prefs["favorite_mood"]
    fav_genre = user_prefs["favorite_genre"]
    mood_entry = context.mood_entry
    genre_entry = context.genre_entry

    if fav_mood and song["mood"] == fav_mood:
        detail = f" ({mood_entry['description']})" if mood_entry else ""
        parts.append(f"its {song['mood']} mood is the one you asked for{detail}")
    elif fav_mood and context.mood_related.get(song["mood"], 0) > 0:
        detail = f" - you asked for {fav_mood}: {mood_entry['description']}" if mood_entry else ""
        parts.append(f"its {song['mood']} mood is a close relative of {fav_mood}{detail}")

    if fav_genre and song["genre"] == fav_genre:
        detail = f" ({genre_entry['description']})" if genre_entry else ""
        parts.append(f"it is {song['genre']}, your favorite genre{detail}")
    elif fav_genre and context.genre_related.get(song["genre"], 0) > 0:
        parts.append(f"{song['genre']} is closely related to your favorite genre, {fav_genre}")

    parts.append(
        f"its energy ({song['energy']:.2f}) is "
        f"{'close to' if abs(song['energy'] - user_prefs['target_energy']) <= 0.2 else 'far from'} "
        f"your target ({user_prefs['target_energy']:.2f})"
    )

    return f"{song['title']} by {song['artist']}: " + "; ".join(parts) + "."


def recommend_songs_rag(
    user_prefs: Dict,
    songs: List[Dict],
    kb: KnowledgeBase,
    k: int = 5,
) -> List[Recommendation]:
    """Retrieve knowledge for the profile, then score, rank and explain songs."""
    context = kb.retrieve(user_prefs["favorite_genre"], user_prefs["favorite_mood"])

    ranked = []
    for song in songs:
        score, reasons = score_song_rag(user_prefs, song, context)
        ranked.append((song, score, reasons))
    ranked.sort(key=lambda item: item[1], reverse=True)

    results = []
    for song, score, reasons in ranked[:k]:
        confidence = round(min(score / MAX_SCORE, 1.0), 2)
        label = confidence_label(confidence)
        flags = []
        if label == "low":
            flags.append("low confidence: weak match on genre and mood, treat as a loose suggestion")
        energy_gap = abs(song["energy"] - user_prefs["target_energy"])
        if energy_gap > ENERGY_GAP_WARNING:
            flags.append(
                f"energy gap: song energy {song['energy']:.2f} is far from your target "
                f"{user_prefs['target_energy']:.2f}, so label matches may be outweighing what you want"
            )
        results.append(
            Recommendation(
                song=song,
                score=round(score, 2),
                confidence=confidence,
                confidence_label=label,
                reasons=reasons,
                explanation=build_explanation(user_prefs, song, context),
                flags=flags,
            )
        )

    if results:
        logger.info(
            "Top pick %s (score %.2f, confidence %.2f %s)",
            results[0].song["title"], results[0].score, results[0].confidence, results[0].confidence_label,
        )
    return results


# ---------------------------------------------------------------------------
# Object-oriented wrapper used by the tests.
# ---------------------------------------------------------------------------

def _song_to_dict(song: Song) -> Dict:
    return {
        "id": song.id,
        "title": song.title,
        "artist": song.artist,
        "genre": song.genre,
        "mood": song.mood,
        "energy": song.energy,
        "tempo_bpm": song.tempo_bpm,
        "valence": song.valence,
        "danceability": song.danceability,
        "acousticness": song.acousticness,
    }


class Recommender:
    """OOP wrapper around the scoring logic.

    With a knowledge base it ranks using retrieval-aware scoring; without
    one it falls back to the original VibeFinder 1.0 scoring.
    """

    def __init__(self, songs: List[Song], knowledge_base: Optional[KnowledgeBase] = None):
        self.songs = songs
        self.kb = knowledge_base

    @staticmethod
    def _prefs(user: UserProfile) -> Dict:
        return {
            "favorite_genre": user.favorite_genre,
            "favorite_mood": user.favorite_mood,
            "target_energy": user.target_energy,
            "target_tempo": user.target_tempo if user.target_tempo is not None else DEFAULT_TEMPO,
        }

    def recommend(self, user: UserProfile, k: int = 5) -> List[Song]:
        prefs = self._prefs(user)
        by_id = {song.id: song for song in self.songs}
        song_dicts = [_song_to_dict(song) for song in self.songs]

        if self.kb is not None:
            ranked = [rec.song for rec in recommend_songs_rag(prefs, song_dicts, self.kb, k=k)]
        else:
            ranked = [song for song, _, _ in recommend_songs(prefs, song_dicts, k=k)]
        return [by_id[song["id"]] for song in ranked]

    def explain_recommendation(self, user: UserProfile, song: Song) -> str:
        prefs = self._prefs(user)
        song_dict = _song_to_dict(song)
        if self.kb is not None:
            context = self.kb.retrieve(prefs["favorite_genre"], prefs["favorite_mood"])
            return build_explanation(prefs, song_dict, context)
        _, reasons = score_song(prefs, song_dict)
        return f"{song.title} by {song.artist}: " + ", ".join(reasons)
