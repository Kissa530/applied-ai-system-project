"""
Retrieval layer for the Music Recommender (the "R" in RAG).

The knowledge base (knowledge/taste_kb.json) holds a short description and a
"related" map for every mood and genre. The recommender looks these entries
up before it scores songs, and uses what it finds in two ways:

  1. the related-label weights give partial credit to similar moods/genres
     (so "chill" and "relaxed" are no longer a total miss), and
  2. the retrieved descriptions are used to write each explanation.

Retrieval is fully offline: exact lookup for a structured profile, and a
small TF-IDF-style keyword search for a free-text request.
"""

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_KB_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "taste_kb.json"

DEFAULT_ENERGY = 0.5
DEFAULT_TEMPO = 100

_STOPWORDS = {
    "a", "an", "and", "the", "to", "for", "of", "in", "on", "with", "that",
    "is", "it", "me", "my", "i", "some", "something", "music", "song", "songs",
    "want", "like", "need", "good", "feel", "feels", "make", "makes", "you",
    "your", "or", "as", "be", "at", "up", "by", "from", "into", "no", "so",
}


class KnowledgeBaseError(Exception):
    """Raised when the knowledge base file is missing or malformed."""


@dataclass
class RetrievalContext:
    """What retrieval found for one user profile."""

    genre_entry: Optional[Dict] = None
    mood_entry: Optional[Dict] = None
    genre_related: Dict[str, float] = field(default_factory=dict)
    mood_related: Dict[str, float] = field(default_factory=dict)

    @property
    def found_anything(self) -> bool:
        return self.genre_entry is not None or self.mood_entry is not None


def _stem(token: str) -> str:
    """Very small suffix stripper so 'relaxing' and 'relax' can match."""
    for suffix in ("ing", "ers", "er", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [_stem(t) for t in tokens if t not in _STOPWORDS]


class KnowledgeBase:
    """Loads the taste knowledge base and answers lookups and searches."""

    def __init__(self, data: Dict):
        if "moods" not in data or "genres" not in data:
            raise KnowledgeBaseError("knowledge base needs 'moods' and 'genres' sections")
        self.moods: Dict[str, Dict] = data["moods"]
        self.genres: Dict[str, Dict] = data["genres"]
        self._docs = self._build_index()

    @classmethod
    def load(cls, path: Optional[str] = None) -> "KnowledgeBase":
        kb_path = Path(path) if path else DEFAULT_KB_PATH
        try:
            with open(kb_path, encoding="utf-8") as kb_file:
                data = json.load(kb_file)
        except FileNotFoundError as exc:
            raise KnowledgeBaseError(f"knowledge base not found: {kb_path}") from exc
        except json.JSONDecodeError as exc:
            raise KnowledgeBaseError(f"knowledge base is not valid JSON: {kb_path}") from exc
        kb = cls(data)
        logger.info("Loaded knowledge base %s (%d moods, %d genres)", kb_path, len(kb.moods), len(kb.genres))
        return kb

    # ---- exact lookup (structured profile) ------------------------------

    def get_mood(self, name: Optional[str]) -> Optional[Dict]:
        return self.moods.get(name) if name else None

    def get_genre(self, name: Optional[str]) -> Optional[Dict]:
        return self.genres.get(name) if name else None

    def retrieve(self, favorite_genre: Optional[str], favorite_mood: Optional[str]) -> RetrievalContext:
        """Look up the knowledge base entries for a user's genre and mood."""
        genre_entry = self.get_genre(favorite_genre)
        mood_entry = self.get_mood(favorite_mood)
        context = RetrievalContext(
            genre_entry=genre_entry,
            mood_entry=mood_entry,
            genre_related=dict(genre_entry["related"]) if genre_entry else {},
            mood_related=dict(mood_entry["related"]) if mood_entry else {},
        )
        logger.info(
            "Retrieved genre=%s (%s) mood=%s (%s)",
            favorite_genre, "hit" if genre_entry else "miss",
            favorite_mood, "hit" if mood_entry else "miss",
        )
        return context

    # ---- free-text search ----------------------------------------------

    def _build_index(self) -> Dict[Tuple[str, str], set]:
        docs: Dict[Tuple[str, str], set] = {}
        for kind, entries in (("mood", self.moods), ("genre", self.genres)):
            for name, entry in entries.items():
                text = " ".join([name, " ".join(entry.get("keywords", [])), entry.get("description", "")])
                docs[(kind, name)] = set(_tokenize(text))
        return docs

    def _idf(self, token: str) -> float:
        doc_count = len(self._docs)
        containing = sum(1 for tokens in self._docs.values() if token in tokens)
        return math.log((doc_count + 1) / (containing + 1)) + 1.0

    def search(self, query: str, kind: Optional[str] = None) -> List[Tuple[str, str, float]]:
        """Rank knowledge base entries against a free-text request.

        Returns (kind, name, score) tuples, best first, only entries that
        share at least one meaningful word with the query.
        """
        query_tokens = set(_tokenize(query))
        results = []
        for (entry_kind, name), tokens in self._docs.items():
            if kind and entry_kind != kind:
                continue
            overlap = query_tokens & tokens
            if overlap:
                results.append((entry_kind, name, sum(self._idf(t) for t in overlap)))
        results.sort(key=lambda item: item[2], reverse=True)
        return results

    def _named_genre(self, query: str) -> Optional[str]:
        """Return a genre only when the request actually names it.

        A genre is a strong claim ("I want metal"), so a stray keyword such as
        "loud" must not set one. When several names match ("indie pop" and
        "pop"), the longest name wins.
        """
        query_tokens = set(_tokenize(query))
        named = [
            name for name in self.genres
            if set(_tokenize(name)) and set(_tokenize(name)) <= query_tokens
        ]
        return max(named, key=len) if named else None

    def profile_from_text(self, query: str) -> Optional[Dict]:
        """Turn a free-text request ("something calm to study to") into a profile.

        Picks the best-matching mood and genre entries; energy and tempo come
        from the retrieved mood entry. Returns None when nothing matches.
        """
        if not query or not query.strip():
            return None

        mood_hits = self.search(query, kind="mood")
        mood = mood_hits[0][1] if mood_hits else None
        genre = self._named_genre(query)
        if mood is None and genre is None:
            logger.warning("Free-text request matched nothing in the knowledge base: %r", query)
            return None

        mood_entry = self.get_mood(mood)

        profile = {
            "favorite_genre": genre,
            "favorite_mood": mood,
            "target_energy": mood_entry["typical_energy"] if mood_entry else DEFAULT_ENERGY,
            "target_tempo": mood_entry["typical_tempo"] if mood_entry else DEFAULT_TEMPO,
        }
        logger.info("Free-text %r -> %s", query, profile)
        return profile
