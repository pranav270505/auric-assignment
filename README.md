# Shift-notes rota puzzles under a fixed weak model

Granite (`ibm-granite/granite-4.2-8b`) reads each line of the notes into a
small rule language; code validates the rules, enumerates every schedule and
decides unique / ambiguous / inconsistent, citing the smallest minimal conflict
verbatim. Design and evidence: `WRITEUP.md`. Tables: `ABLATION.md`.

## Setup

Python 3.10+ (developed on 3.11 and 3.13).

```
pip install -r requirements.txt
```

Credentials come from a `.env` file in the repo root or from the environment.
`.env` is git-ignored and is not part of this submission.

| Setting | Variables read, first match wins | Default |
|---|---|---|
| API key | `OPENROUTER_API_KEY`, `API_KEY`, `OPENAI_API_KEY` | none (required) |
| Endpoint | `OPENROUTER_BASE_URL`, `BASE_URL`, `OPENAI_BASE_URL` | none (required) |
| Model | not configurable | `ibm-granite/granite-4.2-8b` |

## Run

```
./run items.json --budget <1x|3x|10x> --out answers.json
python3 score.py visible_key.json answers.json
```

Every call sends `X-Item-Id`, temperature 1.0, top_p 0.95 and reasoning off.
Every attempt, including a retry, is charged to the item's cap, so an item
never exceeds its budget. Optional flags: `--log-dir DIR` saves raw model
outputs, `--ablate NAME` switches one component off, `--workers N` sets
parallelism.

## Results

Macro exact match, mean of the runs shown in brackets:

| Set | 1x | 3x | 10x |
|---|---|---|---|
| Visible (`items.json`) | 92.5 (2) | 95.8 (2) | 100.0 (1) |
| Visible puzzles, clues reworded (`dev/paraphrase`) | 90.0 (2) | 93.3 (2) | – |
| New puzzles, wording pool A (`dev/fresh`) | 84.2 (2) | 88.3 (2) | – |
| New puzzles, wording pool B (`dev/fresh2`) | 65.0 (2) | 70.8 (2) | – |

`results/<set>_<budget>.json` holds the answers of the submitted system from
one live run per cell. For runs made before the final post-processing step
(the core flip) was added, those answers are the final code re-deciding that
run's saved model outputs; that step makes no model calls, so this equals a
live run of the final system.

## Ablation

```
python3 ablate.py live --budgets 1x,3x,10x
python3 ablate.py offline --items <items.json> --key <key.json> --logs <log dir>
```

`live` re-runs the system through the endpoint once per component switched off.
`offline` re-decides saved model outputs with one post-processing component off
at a time, with no model calls, so differences come from the component alone.
`python3 dev/final_run.py` reproduces every number above in one resumable
command (about 4,200 calls).

## Tests

```
python3 -m unittest discover -s tests     # 21 tests, no model calls
python3 dev/check_gold.py                 # solver scores 100% on gold rules
python3 dev/mock_test.py                  # end-to-end with a fake client, budget checks
```

## Layout

| Path | Role |
|---|---|
| `run`, `run.py` | entrypoint: parallel over items, budget check, `--ablate` switches |
| `client.py` | the only place the model is called: `X-Item-Id`, fixed sampling, per-item cap |
| `extract.py` | prompt, hedge and restatement stripping, robust parser, grounding, re-grounding |
| `solver.py` | header parsing, rule language, 720-schedule enumeration, smallest minimal conflict |
| `pipeline.py` | per-item flow: samples, vote, rescue, solve, core flip |
| `ablate.py` | live and paired ablation tables |
| `tests/` | unit tests |
| `results/` | answers of the submitted system on each test set |
| `dev/` | development only, never used to answer: gold labeller, analysis, test-set generators (`make_paraphrase_set.py`, `make_fresh_set.py`, `check_fresh.py`), the generated sets, `final_run.py` |
