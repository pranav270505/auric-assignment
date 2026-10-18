# Architecture under a fixed weak model

**Results.** Granite alone scores 0.0% and the frontier one-shot reference is 67.5%. The submitted system, macro exact match, with the number of live runs in brackets:

| | 1x | 3x | 10x |
|---|---|---|---|
| Visible set | 92.5 (2) | 95.8 (2) | 100.0 (1) |
| Fresh puzzles, unseen wording (pool A) | 84.2 (2) | 88.3 (2) | – |
| Fresh puzzles, unseen wording (pool B) | 65.0 (2) | 70.8 (2) | – |
| Visible puzzles, every clue reworded | 90.0 (2) | 93.3 (2) | – |

The hidden set is new puzzles in unseen wording, so the fresh rows, not the visible one, are my estimate of it. Every item uses exactly its budget. On the visible set, every item correctly called inconsistent also cites a valid minimal conflict.

## 1. The design: the model reads, code reasons

**Constraint extraction is model-based; everything after it is symbolic.** Granite translates each numbered line into a seven-form rule language (`at`, `not_at`, `on`, `not_on`, `before`, `right_before`, `between`, or `noise`; a term can be `@station`, "whoever holds it"). Code validates the rules, enumerates all 720 schedules (120 block orders × 6 station assignments) as bitmasks, decides the case from the count (0 / 1 / 2–4), and for 0 finds the smallest minimal conflicting set by increasing-size search. Citations are copied from the notes by line number, so they are verbatim by construction. The model never quotes, counts or solves.

I built the symbolic half first. A dev-only regex labeller (never in the answer path, and deliberately overfit) gives gold rules for all 537 clue lines, and the solver scores 100% on them. Every later error is therefore an extraction error, and saved model outputs can be re-decided offline under any post-processing change. That made most experiments free.

## 2. How I characterised the model

A minimal harness (one call, strict parser) scored 71.7%. Against the gold labels:

- **Format drift.** Despite "nothing else", Granite comments on its rules and writes drafts. A strict parser lost 6 items.
- **Neighbour copying.** The right rule shape with one name taken from an adjacent line ("Meera relieves Ingrid directly" → `right_before(Ingrid, Nadia)`).
- **Direction** is its hardest skill: `before(A, B)` versus `before(B, A)`.
- **Noise bias.** Misses outnumber invented rules about 3:1.

The decisive measurement came from sampling each item several times: **the errors are systematic, not random.** At 5 samples, 14 lines were wrong in every sample; "*Put* X between A and B, though *not necessarily* next to either" was read as noise 4–5 times out of 5. That is why plain voting gave a flat curve (81.7 / 81.7 / 80.0 at 1x / 3x / 10x): a majority of identical misreadings is still a misreading. Everything after that targets the systematic part.

## 3. Why each component exists

Live ablation on the visible set (`ABLATION.md` §1), and the paired ablation that re-decides the same model outputs with one component off on all four test sets (§2):

| Component | Visible live 1x / 3x without it | Effect elsewhere (paired) | Why it exists |
|---|---|---|---|
| full system | 93.3 / 96.7 | | |
| hedge and restatement stripping | 81.7 / 95.0 | prompt-side, live only | Hedges carry no content; removing them in code is exact and spares the small model's attention |
| grounding + re-grounding | 85.0 / 86.7 | −2 to −13 on every set | A rule may only name entities in its own sentence; if exactly one name is foreign and the sentence has exactly one unused entity of that kind, swap it in |
| robust parser | 93.3 / 96.7 | −1.7 to −6.7 | Takes the rule before any comment and the last answer when the model revises a line |
| voting | – / 95.0 | −1.7 to −8.3 at 3x | Removes the random part of the error |
| reversed-order samples | – / 98.3 | −1.7 to −5.0 | Alternate calls list the lines bottom-up, so position-driven slips differ between samples |
| rescue | – / 95.0 | −1.7 | When most samples say noise but one gives a grounded rule that keeps the item solvable, keep it; targets the noise bias that voting preserves |
| core flip | 93.3 / 96.7 | −1.6 to −1.7 on fresh and paraphrase | The brief says every real conflict set in the visible set has at least three statements, so a one- or two-line contradiction is a misread, almost always a reversed rule; reverse it if that leaves 1–4 schedules. This assumes the held-out set follows the same rule |

No component improves any of the 9 paired columns (including visible 10x) by being removed. The prompt is a component too, changed once on evidence: I removed "write noise when unsure" (it fed the noise bias) and stated the principles behind the measured blind spots (instructions such as "put X…" describe the rota; "not necessarily next to" is not doubt; a refused request is noise). Per-line error halved, from 3.1% to 1.5%.

## 4. Built, measured, removed

- **Self-check repair.** On 0 solutions the bad rule must sit in the conflict core, so I re-asked the model about those lines; on more than 4, about the noise lines. It answered "ok" to almost everything and twice turned a right rule into a wrong one: it shares the extractor's blind spots. Live 3x with it: 96.7, no gain. Kept behind `--ablate with_repair`.
- **Named-slot output**, `before(earlier=A, later=B)`, to stop argument-order reversals. Live, same items: +11.7 on one unseen wording pool (reproduced in a second pair of runs) but −5 to −8 on two others. It shifted which wordings get reversed rather than reducing reversals, and on held-out wording the two formats tie (79.5 vs 78.3 at 1x). I kept the simpler format. Without four test sets I would have shipped a change that fit one wording pool.
- **Reading wishes as negations** ("turned down for 15:00" as `not_at`). It creates extra, smaller conflict sets and cost an inconsistent item. Wishes stay noise.
- **Other post-processing** (using other samples' readings on any contradiction, mixing both output formats across samples) helped some sets and hurt others, or won only when picked after seeing results. Not adopted.

## 5. Where it breaks

- **Unfamiliar wording of a relation.** Pool B's "Rhea gets going earlier than Tara" and "Kofi goes, and Elif is on in the adjoining block" drive 20–27% error on ordered clues, against under 2% for most other clue types.
- **A reversed rule creates a false contradiction.** On held-out sets the main error is a unique or ambiguous item declared inconsistent. The core flip catches only the one- and two-line cases.
- **Ambiguous items suffer most** (pool B: 50%). A reversed rule removes a valid schedule, and a missed rule adds an invalid one; either breaks exact match.
- **Extra calls add 3–6 points on every set** but cannot fix a misreading the model makes every time.
- **Small sets.** One item is 1.7 points, and two 1x runs of one system have differed by 6.7, so I report two runs where I could and trust only differences that hold across sets.

## 6. If the notes were written by different people

The architecture transfers; some symbolic pieces would not. Grounding assumes names appear literally, so pronouns, nicknames and typos ("she relieves him") would make correct rules look ungrounded. The header is parsed by regex, and one statement per line is assumed, while people write "Priya's on intake, right after Tomas". Fixes: a model pass that resolves references and splits sentences into atomic statements, a model fallback for the header, and fuzzy name matching. The solver, the minimal-conflict search and verbatim citation are unchanged. The deeper risk is that real notes carry real uncertainty, whereas here hedges carry full force by rule.

## 7. With a month

1. **Diversify the questions, not just the samples.** Errors are systematic, so asking the same question again has limited value. Per-relation probes for disputed lines ("Who works earlier, Priya or Tomas?"), model-written paraphrases of a line, and mixed output formats, with the solver picking the most-supported consistent reading. Mixing formats looked promising offline (+1.7) but I chose that mix after seeing results, so it needs a clean test.
2. **Grammar-constrained decoding**, if the serving stack allows it, to remove format errors at the source.
3. **More held-out wording pools** and some human-written items, tuning only on a development split. The difference between pools A and B (84 vs 65) shows how much the score depends on phrasing.
4. **CP-SAT and coreference** for longer rotas and free-form notes.

**Reproduce.** `./run items.json --budget 1x --out answers.json`; `python3 ablate.py live` (live ablation); `python3 ablate.py offline --logs <dir>` (paired); `python3 dev/final_run.py` (every number above). Python 3.10+; dependencies pinned in `requirements.txt`.
