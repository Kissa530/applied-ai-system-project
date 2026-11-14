"""
Evaluation harness for the Music Recommender (VibeFinder 2.0).

Runs the system on predefined inputs, checks each result against an
expectation, and prints a PASS/FAIL table with confidence scores, followed
by a before/after comparison of the original scoring (no retrieval) versus
the retrieval-augmented scoring.

    python -m src.evaluate

The exit code is 1 if any check fails, so it can run in automation.
"""

import logging
import os
import sys
from typing import Callable, Dict, List, Tuple

from src.guardrails import InvalidProfileError, validate_profile
from src.main import BAD_PROFILES, LOG_PATH, PROFILES
from src.recommender import load_songs, recommend_songs, recommend_songs_rag
from src.retriever import KnowledgeBase

Check = Callable[[], Tuple[bool, str]]


def titles_rag(songs: List[Dict], kb: KnowledgeBase, prefs: Dict, k: int = 5) -> List[str]:
    return [r.song["title"] for r in recommend_songs_rag(prefs, songs, kb, k=k)]


def titles_base(songs: List[Dict], prefs: Dict, k: int = 5) -> List[str]:
    return [song["title"] for song, _, _ in recommend_songs(prefs, songs, k=k)]


def build_checks(songs: List[Dict], kb: KnowledgeBase) -> List[Tuple[str, Check]]:
    def top_pick(name: str, expected: str) -> Check:
        def run():
            top = recommend_songs_rag(PROFILES[name], songs, kb, k=1)[0]
            return top.song["title"] == expected, (
                f"top pick {top.song['title']!r}, score {top.score:.2f}, "
                f"confidence {top.confidence:.2f} ({top.confidence_label})"
            )
        return run

    def rag_changes_ranking():
        prefs = PROFILES["Chill Lofi"]
        base, rag = titles_base(songs, prefs), titles_rag(songs, kb, prefs)
        return base != rag and "Coffee Shop Stories" in rag and "Coffee Shop Stories" not in base, (
            f"baseline {base} -> rag {rag}"
        )

    def happy_pop_fixed():
        rag = titles_rag(songs, kb, PROFILES["High-Energy Pop"])
        ok = rag.index("Rooftop Lights") < rag.index("Gym Hero")
        return ok, f"Rooftop Lights at #{rag.index('Rooftop Lights') + 1}, Gym Hero at #{rag.index('Gym Hero') + 1}"

    def energy_gap_flagged():
        top = recommend_songs_rag(PROFILES["Conflicting: High Energy + Melancholy"], songs, kb, k=1)[0]
        flagged = any("energy gap" in flag for flag in top.flags)
        return flagged, f"flags on {top.song['title']!r}: {top.flags}"

    def kb_consistent():
        problems = []
        for kind, table in (("mood", kb.moods), ("genre", kb.genres)):
            for name, entry in table.items():
                for related, weight in entry["related"].items():
                    if related not in table:
                        problems.append(f"{kind}:{name}->{related} unknown")
                    if not 0 < weight < 1:
                        problems.append(f"{kind}:{name}->{related} weight {weight}")
        missing = ({s["genre"] for s in songs} - set(kb.genres)) | ({s["mood"] for s in songs} - set(kb.moods))
        problems += [f"catalog label {m!r} not in knowledge base" for m in missing]
        return not problems, "all related labels exist, weights in (0,1), catalog fully covered" if not problems else str(problems)

    def text_study():
        profile = kb.profile_from_text("something calm to study to")
        return bool(profile) and profile["favorite_mood"] == "chill", f"profile {profile}"

    def text_workout():
        profile = kb.profile_from_text("loud gym workout music")
        top2 = titles_rag(songs, kb, validate_profile(profile)[0], k=2) if profile else []
        ok = bool(profile) and profile["favorite_genre"] is None and set(top2) == {"Storm Runner", "Gym Hero"}
        return ok, f"mood {profile and profile['favorite_mood']}, genre {profile and profile['favorite_genre']}, top 2 {top2}"

    def text_gibberish():
        result = kb.profile_from_text("zzzz qqqq")
        return result is None, f"profile_from_text returned {result!r}"

    def rejected(name: str) -> Check:
        def run():
            try:
                validate_profile(BAD_PROFILES[name], set(kb.genres), set(kb.moods))
            except InvalidProfileError as exc:
                return True, f"rejected: {exc}"
            return False, "was accepted but should have been rejected"
        return run

    def clamped():
        clean, warnings = validate_profile(BAD_PROFILES["Energy out of range"], set(kb.genres), set(kb.moods))
        return clean["target_energy"] == 1.0 and any("clamped" in w for w in warnings), f"energy -> {clean['target_energy']}, warnings {warnings}"

    def unknown_labels():
        clean, warnings = validate_profile(BAD_PROFILES["Unknown genre and mood"], set(kb.genres), set(kb.moods))
        top = recommend_songs_rag(clean, songs, kb, k=1)[0]
        ok = len(warnings) == 2 and top.confidence_label == "low"
        return ok, f"{len(warnings)} warnings, top confidence {top.confidence:.2f} ({top.confidence_label})"

    def deterministic():
        a = titles_rag(songs, kb, PROFILES["High-Energy Pop"])
        b = titles_rag(songs, kb, PROFILES["High-Energy Pop"])
        return a == b, "two runs gave identical rankings"

    return [
        ("High-Energy Pop -> Sunrise City first", top_pick("High-Energy Pop", "Sunrise City")),
        ("Chill Lofi -> Midnight Coding first", top_pick("Chill Lofi", "Midnight Coding")),
        ("Deep Intense Rock -> Storm Runner first", top_pick("Deep Intense Rock", "Storm Runner")),
        ("Conflicting profile -> Winter Window first (known limitation)",
         top_pick("Conflicting: High Energy + Melancholy", "Winter Window")),
        ("Retrieval changes the Chill Lofi ranking vs baseline", rag_changes_ranking),
        ("Happy-pop: Rooftop Lights now outranks Gym Hero", happy_pop_fixed),
        ("Energy-gap warning fires on the conflicting profile", energy_gap_flagged),
        ("Knowledge base is internally consistent", kb_consistent),
        ("Free text 'calm to study to' -> chill", text_study),
        ("Free text 'loud gym workout' -> intense, no invented genre", text_workout),
        ("Gibberish free text matches nothing", text_gibberish),
        ("Empty profile is rejected", rejected("Empty profile")),
        ("Non-numeric energy is rejected", rejected("Non-numeric energy")),
        ("Out-of-range energy is clamped with a warning", clamped),
        ("Unknown genre and mood warn and give low confidence", unknown_labels),
        ("Results are deterministic", deterministic),
    ]


def print_before_after(songs: List[Dict], kb: KnowledgeBase) -> None:
    print("\nBEFORE (original VibeFinder 1.0 scoring) vs AFTER (retrieval-augmented), top 5")
    print("-" * 78)
    changed = 0
    for name, prefs in PROFILES.items():
        before, after = titles_base(songs, prefs), titles_rag(songs, kb, prefs)
        changed += before != after
        print(f"\n{name}  [{'CHANGED' if before != after else 'same'}]")
        print("  before:", " | ".join(before))
        print("  after: ", " | ".join(after))
    print(f"\nRanking changed for {changed} of {len(PROFILES)} profiles.")


def main() -> int:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    songs = load_songs("data/songs.csv")
    kb = KnowledgeBase.load()

    print("EVALUATION HARNESS")
    print("=" * 78)
    checks = build_checks(songs, kb)
    passed = 0
    for name, check in checks:
        try:
            ok, detail = check()
        except Exception as exc:  # a crash counts as a failed check, not a crashed harness
            ok, detail = False, f"crashed: {exc!r}"
        passed += ok
        logging.getLogger("evaluate").info("%s: %s", "PASS" if ok else "FAIL", name)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}\n       {detail}")

    confidences = [recommend_songs_rag(prefs, songs, kb, k=1)[0].confidence for prefs in PROFILES.values()]
    print("\n" + "=" * 78)
    print(f"SUMMARY: {passed}/{len(checks)} checks passed")
    print(f"Average top-1 confidence across {len(PROFILES)} profiles: {sum(confidences) / len(confidences):.2f}")

    print_before_after(songs, kb)
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
