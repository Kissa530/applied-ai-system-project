"""
Command line runner for the Music Recommender (VibeFinder 2.0).

Default run (retrieval-augmented):
    python -m src.main

Original VibeFinder 1.0 scoring, for comparison:
    python -m src.main --baseline

Free-text request (retrieval turns the words into a profile):
    python -m src.main --ask "something calm to study to"
"""

import argparse
import logging
import os
from typing import Dict, List, Optional

from src.guardrails import InvalidProfileError, validate_profile
from src.recommender import load_songs, recommend_songs, recommend_songs_rag
from src.retriever import KnowledgeBase, KnowledgeBaseError

LOG_PATH = os.path.join("logs", "app.log")

PROFILES = {
    "High-Energy Pop": {
        "favorite_genre": "pop", "favorite_mood": "happy",
        "target_energy": 0.90, "target_tempo": 125,
    },
    "Chill Lofi": {
        "favorite_genre": "lofi", "favorite_mood": "chill",
        "target_energy": 0.40, "target_tempo": 78,
    },
    "Deep Intense Rock": {
        "favorite_genre": "rock", "favorite_mood": "intense",
        "target_energy": 0.92, "target_tempo": 150,
    },
    "Conflicting: High Energy + Melancholy": {
        "favorite_genre": "classical", "favorite_mood": "melancholy",
        "target_energy": 0.90, "target_tempo": 140,
    },
}

# Inputs that should trigger the guardrails.
BAD_PROFILES = {
    "Empty profile": {},
    "Energy out of range": {
        "favorite_genre": "pop", "favorite_mood": "happy",
        "target_energy": 1.8, "target_tempo": 125,
    },
    "Unknown genre and mood": {
        "favorite_genre": "polka", "favorite_mood": "grumpy",
        "target_energy": 0.5, "target_tempo": 100,
    },
    "Non-numeric energy": {
        "favorite_genre": "pop", "favorite_mood": "happy",
        "target_energy": "loud", "target_tempo": 125,
    },
}


def setup_logging() -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def print_profile(user_prefs: Dict) -> None:
    print(f"  Genre: {user_prefs['favorite_genre'] or '(any)'}")
    print(f"  Mood: {user_prefs['favorite_mood'] or '(any)'}")
    print(f"  Target energy: {user_prefs['target_energy']:.2f}")
    print(f"  Target tempo: {user_prefs['target_tempo']:.0f} BPM")


def run_baseline(name: str, user_prefs: Dict, songs: List[Dict]) -> None:
    print(f"\n=== {name} (baseline VibeFinder 1.0) ===")
    print_profile(user_prefs)
    print("\nTop recommendations:")
    for rank, (song, score, explanation) in enumerate(recommend_songs(user_prefs, songs, k=5), start=1):
        print(f"\n{rank}. {song['title']} - {song['artist']} (Score: {score:.2f})")
        for reason in explanation.split(", "):
            print(f"   - {reason}")


def run_rag(name: str, raw_prefs: Dict, songs: List[Dict], kb: KnowledgeBase) -> bool:
    """Validate, retrieve, recommend and print. Returns False if the profile was rejected."""
    print(f"\n=== {name} ===")
    try:
        user_prefs, warnings = validate_profile(raw_prefs, set(kb.genres), set(kb.moods))
    except InvalidProfileError as exc:
        print(f"  REJECTED by guardrail: {exc}")
        return False

    print_profile(user_prefs)
    for warning in warnings:
        print(f"  WARNING: {warning}")

    print("\nTop recommendations:")
    for rank, rec in enumerate(recommend_songs_rag(user_prefs, songs, kb, k=5), start=1):
        print(f"\n{rank}. {rec.song['title']} - {rec.song['artist']} "
              f"(Score: {rec.score:.2f}, Confidence: {rec.confidence:.2f} {rec.confidence_label})")
        print(f"   {rec.explanation}")
        for reason in rec.reasons:
            print(f"   - {reason}")
        for flag in rec.flags:
            print(f"   ! {flag}")
    return True


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Music Recommender (VibeFinder 2.0)")
    parser.add_argument("--baseline", action="store_true", help="use the original VibeFinder 1.0 scoring")
    parser.add_argument("--ask", metavar="TEXT", help="free-text request, e.g. 'something calm to study to'")
    args = parser.parse_args(argv)

    setup_logging()
    logger = logging.getLogger("main")

    songs = load_songs("data/songs.csv")
    print(f"Loaded songs: {len(songs)}")
    logger.info("Loaded %d songs", len(songs))

    if args.baseline:
        for name, user_prefs in PROFILES.items():
            run_baseline(name, user_prefs, songs)
        return

    try:
        kb = KnowledgeBase.load()
    except KnowledgeBaseError as exc:
        logger.error("Knowledge base unavailable: %s", exc)
        print(f"Knowledge base unavailable ({exc}). Falling back to baseline scoring.")
        for name, user_prefs in PROFILES.items():
            run_baseline(name, user_prefs, songs)
        return

    print(f"Loaded knowledge base: {len(kb.moods)} moods, {len(kb.genres)} genres")

    if args.ask:
        profile = kb.profile_from_text(args.ask)
        print(f"\nRequest: {args.ask!r}")
        if profile is None:
            print("  No match in the knowledge base. Try mentioning a mood (calm, upbeat) or a genre.")
            return
        run_rag("Free-text request", profile, songs, kb)
        return

    for name, user_prefs in PROFILES.items():
        run_rag(name, user_prefs, songs, kb)

    print("\n\n##### Free-text requests (retrieval builds the profile) #####")
    for text in ("something calm to study to", "loud gym workout music"):
        profile = kb.profile_from_text(text)
        print(f"\nRequest: {text!r}")
        if profile is None:
            print("  No match in the knowledge base.")
            continue
        run_rag("Free-text request", profile, songs, kb)

    print("\n\n##### Guardrail checks (bad input) #####")
    for name, bad_prefs in BAD_PROFILES.items():
        run_rag(name, bad_prefs, songs, kb)


if __name__ == "__main__":
    main()
