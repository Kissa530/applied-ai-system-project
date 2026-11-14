# Model Card: VibeFinder 2.0

## 1. Model Name and Purpose

**VibeFinder 2.0** is a retrieval-augmented music recommender. Given a listener's favorite genre, mood, target energy and target tempo (or a plain-English request), it looks up what those labels mean in a small knowledge base, scores an 18-song catalog, and returns the top 5 songs with an explanation, a confidence score and warning flags.

It extends my Module 3 project, VibeFinder 1.0, which matched genre and mood as exact labels only. The original scoring is kept unchanged as a baseline (`python -m src.main --baseline`). The original model card is still in this repository's git history.

**Intended use:** classroom exploration of how retrieval changes a recommender's behavior. **Not intended for:** a real music app, judging anyone's taste, or making claims about fairness across groups. It has never been tested with real listeners.

## 2. How It Works (short version)

1. **Guardrails** reject empty or non-numeric profiles, clamp out-of-range numbers, and warn on genres or moods the knowledge base does not know.
2. **Retrieval** reads `knowledge/taste_kb.json` (13 moods, 15 genres) for the listener's genre and mood, including which other labels are related and how strongly.
3. **Scoring** uses the original VibeFinder 1.0 rules (genre +2.0, mood +1.0, energy up to +1.0, tempo up to +0.5) plus partial credit for related labels: related genre = 2.0 × weight, related mood = 1.0 × weight. Every weight is below 1, so an exact match always beats a related one. The maximum score is 4.5.
4. **Output** includes an explanation that quotes the retrieved descriptions, a confidence of score ÷ 4.5 (high ≥ 0.70, medium ≥ 0.45, otherwise low), and flags for low confidence and for a large energy gap.

There is no LLM and no API key. Retrieval is a JSON lookup and explanations are templates, so every result is deterministic and reproducible.

## 3. Limitations and Bias

**The catalog is tiny and uneven.** It has 18 songs across 15 genres. Lofi has 3 songs, pop has 2, and the other 13 genres have exactly 1 each. For most genres a "genre match" means one specific song, so that song tends to win no matter how well it fits the rest of the profile. Any conclusion about the system's quality is a conclusion about 18 songs.

**The knowledge base reflects one author's judgment.** I wrote every description and every related-label weight myself (for example, `lofi → ambient 0.6`, `rock → metal 0.6`). Nothing was learned from listening data, so the weights encode my idea of what counts as "related," and a listener who hears these genres differently gets worse results. The genre list is also narrow: it is made of broad popular-music categories (pop, rock, jazz, country, house and so on) and has no entries for many other musical traditions. A listener whose taste lives outside those labels is not represented.

**Listeners the knowledge base does not know are treated worse.** With an unknown genre and mood ("polka" and "grumpy"), the system can only use exact matching on energy and tempo. In my test the top result had confidence 0.30 (low) and no related-label credit was possible. The system is honest about this with warnings, but it still means the quality of recommendations depends on whether a listener's taste fits my labels.

**Related-genre credit can outweigh a big energy mismatch.** For the Deep Intense Rock profile (target energy 0.92), Glass Cathedral (metal, serene, energy 0.22) ranks #3. It earns +1.20 because metal is related to rock (weight 0.6). It is flagged low confidence with an energy-gap warning, but it still ranks one place above Velvet Meter (funk, energy 0.98), a song whose energy is far closer to what the listener asked for. This is an open problem.

**Confidence can look reassuring while hiding a bad match.** Confidence is the score divided by 4.5, and genre and mood make up 3.0 of that. For the conflicting profile (classical, melancholy, energy 0.90), Winter Window (energy 0.02) wins with confidence 0.71, labeled "high." I added an energy-gap flag that fires on that result, but the confidence label itself is still "high." The flag fixes the warning, not the number.

**Free-text understanding is crude.** Requests are matched to moods by keyword overlap. "Something calm to study to" correctly becomes the chill mood, but a request like "sad rainy day piano" selects no genre, because a genre is only set when its name appears in the text. Anything outside the knowledge base's vocabulary matches nothing and asks the user to name a mood or genre.

**Other limits.** Valence, danceability and acousticness are in the data but unused. Explanations are templated and read formulaically. There is no diversity step, so one genre or artist can fill much of a top 5. The catalog has no lyrics, language, artist popularity or listening history.

## 4. Could It Be Misused?

This is a small, offline, low-stakes system, so the realistic risks are modest, but they are real:

- **Treating confidence as accuracy.** A "high" label can sit on a poor match (Winter Window). Someone could read 0.92 average confidence as "92% correct." *Prevention:* the README and this card say what the number measures, the energy-gap flag is shown next to the result, and the output is designed for a human to read the flags and decide.
- **Using it to judge people.** A recommender built on 18 songs and one author's weights says nothing about anyone's taste, background or character. *Prevention:* the intended-use statement rules this out explicitly.
- **Steering recommendations.** Because the related-label weights are hand-edited in a JSON file, whoever controls that file controls what gets promoted. In a real product, this could be used to quietly push particular genres or artists. *Prevention:* the knowledge base is plain text in version control, so every change is visible in git history, and the evaluation harness has a consistency check (all related labels exist, all weights between 0 and 1, the catalog fully covered) that fails on malformed edits. It does not detect a deliberate but valid bias.
- **Privacy if it were deployed.** The logger writes listener profiles to `logs/app.log`. That is fine for synthetic test profiles, but with real users it would store personal preference data. `logs/` is gitignored so logs are not committed. A real deployment would need a retention and consent policy, which this project does not have.
- **Unsafe input.** Free text is only tokenized and compared with keywords, never executed, and the knowledge base is read as JSON data. Empty, non-numeric and out-of-range input is rejected or clamped before scoring.

## 5. What Surprised Me While Testing

- **Clean-looking results hid real problems.** All 50 tests and all 16 evaluation checks passed, and average top-1 confidence was 0.92, but reading the actual recommendations showed two issues that no passing test had caught: "loud gym workout music" selected *metal* (because "loud" is a metal keyword), and Winter Window got "high" confidence for a high-energy listener. The first is fixed (a genre is only set when it is named in the text). The second is flagged, not fully fixed.
- **Retrieval changed more than the top pick.** The top-1 song stayed the same for all four profiles (scores unchanged: Sunrise City 4.38, Midnight Coding 4.48, Storm Runner 4.48, Winter Window 3.18), but the rest of the list changed for 4 of 4 profiles. In Chill Lofi, Coffee Shop Stories replaced Blue Hour Train at #5. In High-Energy Pop, Rooftop Lights and Gym Hero swapped places at #2 and #3.
- **Keeping the baseline paid off.** Because the original scoring stayed intact, every claimed improvement could be compared against it instead of just asserted.
- **My first tests were not testing the system.** The two original tests passed even though the `Recommender` class was an unfinished stub, so I replaced them with tests that fail on that stub.

## 6. Reliability Evidence

Automated results (all in the README): 50 pytest tests pass, the evaluation harness passes 16 of 16 checks, and average top-1 confidence across the four profiles is 0.92. The table below also includes my own review of cases that the harness does not treat as failures.

| Test input | Evaluation criteria | Result |
|---|---|---|
| Empty profile | Rejected with a clear message | Pass |
| `target_energy = "loud"` | Rejected, not scored | Pass |
| `target_energy = 1.8` | Clamped to 1.0 with a warning | Pass |
| Genre "polka", mood "grumpy" | Warns twice, continues, low confidence | Pass (top confidence 0.30) |
| "something calm to study to" | Maps to the chill mood, no invented genre | Pass |
| "loud gym workout music" | Mood intense, no genre invented | Pass after a fix (originally selected metal) |
| "zzzz qqqq" | Matches nothing, asks user to name a mood or genre | Pass |
| Chill Lofi vs. baseline | Ranking changes through retrieval | Pass (Coffee Shop Stories enters the top 5) |
| Conflicting profile (classical, melancholy, energy 0.90) | Top result should not be a near-silent song | Fail, known limitation (Winter Window first, "high" 0.71, energy-gap flag shown) |
| Deep Intense Rock | No low-energy song in the top 3 | Fail, open limitation (Glass Cathedral at #3) |
| "sad rainy day piano" | Should lean classical | Fail (no genre selected) |

## 7. Collaboration with AI

**How I worked with AI:** I used Claude to build the RAG system, tests, diagram and README drafts. I ran everything on my own machine, checked the outputs for tests and evaluate, and pushed the commits myself. Claude also packaged the new files as a zip and gave me a `tar -xf` command to unpack them into the repo, which was much faster than adding each file by hand.

**Suggestion I accepted:** Claude drew the architecture diagram as a Mermaid file and redrew it after the code changed. I questioned whether I should write it myself, but kept it because the brief only requires a Mermaid file and I checked that it matched my project.

**Suggestion I did not accept as written:** When bare `pytest` failed with `ModuleNotFoundError: No module named 'src'`, Claude offered to add a `pytest.ini`. I used `python -m pytest` instead, which gives the same result without an extra file. The README tells readers to use it and says why. I felt `python -m pytest` was easier because it was more consistent with the original project (VibeFinder 1.0).

## 8. What I Would Do Next

- Add many more songs, so most genres have more than one entry and the results are no longer decided by single songs.
- Make confidence account for the energy fit, so a near-silent song cannot be "high" confidence for a high-energy request.
- Cap or scale related-genre credit when the energy gap is large (the Glass Cathedral case).
- Use valence, danceability and acousticness, and add a diversity step to the top 5.
- Test with real listeners instead of hand-picked profiles, and review the related-label weights with more than one person.