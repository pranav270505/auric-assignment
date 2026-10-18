"""
Per-item pipeline: extract (model) -> parse -> ground -> vote -> rescue -> solve (code).

Budget use:
  1x   one extraction.
  3x   three extractions (alternating forward / reversed line order), per-line
       majority vote, then rescue of minority rules on majority-noise lines.
  10x  the same with ten extractions.

Core flip: the brief states that on the visible set every real conflict set
has at least three statements; the held-out set is assumed to be generated the
same way. Under that assumption a contradiction the solver traces to one or two lines is an
extraction error, nearly always a reversed ordering. In that case only, each
ordered rule in that small core is reversed in turn and the first reversal
that leaves 1-4 schedules is kept. Measured on 23 saved live runs: 8 better,
0 worse.

Named-slot output (--ablate with_slots, before(earlier=A, later=B)) was built
and measured on four sets: +3 to +12 points on two, -5 to -8 on the other two,
a tie on held-out wording, so the simpler positional format stays the default.

The solver-guided self-check repair (re-asking the model about the conflict
core, or about noise lines when >4 solutions) was built, measured and removed
from the defaults: Granite confirmed its own readings almost every time and
twice "corrected" a right rule into a wrong one. It remains behind
--ablate with_repair so the ablation can be re-run.
"""
import re
from collections import Counter

from client import call_model, calls_left
from extract import build_messages, clean_line, grounded, parse_line_content, parse_output
from solver import body_lines, parse_header, solve

BUDGET_CAP = {"1x": 1, "3x": 3, "10x": 10}

DEFAULT_CONFIG = {
    "strip_hedges": True,     # code removes restatement prefixes and hedges
    "samples": {"1x": 1, "3x": 3, "10x": 10},
    "vote": True,             # per-line majority over samples
    "diverse": True,          # alternate samples list the lines in reverse order
    "rescue": True,           # restore minority rules on majority-noise lines
    "repair_unsat": False,    # REMOVED by default: self-check said "ok" or broke rules
    "repair_wide": False,     # REMOVED by default: same blind spot as extraction
    "robust_parse": True,     # tolerate comments / drafts in model output
    "slots": False,           # named-slot output (--ablate with_slots); tied on held-out sets
    "core_flip": True,        # a 1-2 line "contradiction" is a misread: try reversing it
    "ground": True,           # reject rules naming people not in the sentence
    "reground": True,         # ...unless one wrong filler can be swapped for
                              # the one unused entity the sentence does name
}


def _vote(samples, n_lines):
    """Per-line majority over parsed samples. Ties go to the rule-bearing
    reading seen first (a missed rule is caught by nothing at 1-4 solutions,
    while a spurious one is exposed by an unsatisfiable core)."""
    final, agree = {}, {}
    for i in range(n_lines):
        readings = [frozenset(s.get(i, [])) for s in samples]
        if not readings:
            continue
        c = Counter(readings)
        top = max(c.values())
        tied = [r for r in readings if c[r] == top]
        best = next((r for r in tied if r), tied[0])
        final[i] = sorted(best)
        agree[i] = top / len(readings)
    return final, agree


def _rescue(item, samples, rules):
    """Granite's dominant error is calling a real constraint noise, and it does
    so systematically, so a majority vote keeps the miss. Where the majority
    reading of a line is noise but some sample gave a grounded rule, restore
    the most common such rule, provided the item stays satisfiable. An item
    the vote already finds unsatisfiable is left alone: adding rules cannot
    help it and could only move the conflict core."""
    rules = dict(rules)
    _, diag = solve(item, rules)
    rescued = []
    if diag["n_solutions"] == 0:
        return rules, rescued
    for i in range(len(body_lines(item["text"]))):
        if rules.get(i):
            continue
        cands = Counter(frozenset(s.get(i, [])) for s in samples if s.get(i))
        for reading, _ in cands.most_common():
            trial = dict(rules)
            trial[i] = sorted(reading)
            if solve(item, trial)[1]["n_solutions"] >= 1:
                rules = trial
                rescued.append(i)
                break
    return rules, rescued


def _core_flip(item, rules, answer, diag):
    """Reverse one ordered rule inside a 1-2 line conflict core, if that
    leaves 1-4 schedules. Returns the (possibly unchanged) answer."""
    for i in diag["core"]:
        for k, r in enumerate(rules.get(i, [])):
            if r[0] not in ("before", "right_before"):
                continue
            trial = dict(rules)
            trial[i] = list(rules[i])
            trial[i][k] = (r[0], r[2], r[1])
            a2, d2 = solve(item, trial)
            if 1 <= d2["n_solutions"] <= 4:
                return a2, d2, trial, {"line": i, "from": _fmt(r), "to": _fmt(trial[i][k])}
    return answer, diag, rules, None


VERIFY_SYSTEM = """You check translations of shift-note sentences into scheduling rules.

Rule meanings:
  at(P, T) P works block T | not_at(P, T) P does not work block T
  on(P, S) P is on station S | not_on(P, S) P is not on station S
  before(A, B) A's block is earlier than B's (any gap)
  right_before(A, B) A's block is immediately before B's
  between(X, A, B) X's block lies between A's and B's, either order
  @S means "whoever is on station S"; noise means no definite current fact.

Hedges ("I think", "fairly sure"...) do not weaken a claim. Wishes, requests, past arrangements, hypotheticals and unsettled discussion are noise.
Check especially the DIRECTION of before / right_before, and whether "directly / immediately / back to back" is present.

For each numbered line answer exactly one of:
L<n>: ok
L<n>: <corrected rule>
Nothing else."""

WIDE_SYSTEM = VERIFY_SYSTEM.replace(
    "You check translations of shift-note sentences into scheduling rules.",
    "These sentences were all marked noise, but the rules they produced allow "
    "too many schedules, so some of them probably state a real constraint. "
    "Re-read each one and say whether it is truly noise.")


def _review(item_id, cap, system, roster, lines, cleaned, idx, current, cfg):
    rows = []
    for i in idx:
        rule = "; ".join(_fmt(r) for r in current.get(i, [])) or "noise"
        rows.append(f"L{i + 1}: \"{cleaned[i]}\"  ->  {rule}")
    user = (f"Roster: people {', '.join(roster['people'])}; blocks "
            f"{', '.join(roster['blocks'])}; stations {', '.join(roster['stations'])}.\n\n"
            + "\n".join(rows))
    text = call_model(item_id, [{"role": "system", "content": system},
                                {"role": "user", "content": user}], cap=cap,
                      max_tokens=600)
    changes = {}
    for raw in (text or "").splitlines():
        m = re.match(r"^\s*L\s*(\d+)\s*[:.)\-]\s*(.*?)\s*$", raw, re.I)
        if not m:
            continue
        i = int(m.group(1)) - 1
        if i not in idx:
            continue
        ans = m.group(2).strip().rstrip(".")
        if re.match(r"^(ok|correct|yes)\b", ans, re.I):
            continue
        rules = parse_line_content(ans, roster, cfg["robust_parse"])
        if rules is None:
            continue
        if cfg["ground"]:
            rules = [r for r in rules if grounded(r, lines[i])]
        if sorted(rules) != sorted(current.get(i, [])):
            changes[i] = rules
    return text, changes


def _fmt(r):
    return f"{r[0]}({', '.join(r[1:])})"


def run_item(item, budget, config=None):
    cfg = dict(DEFAULT_CONFIG, **(config or {}))
    iid = item["id"]
    cap = BUDGET_CAP[budget]
    roster = parse_header(item["text"])
    lines = body_lines(item["text"])
    cleaned = [clean_line(l, cfg["strip_hedges"]) for l in lines]
    log = {"id": iid, "budget": budget, "raw": [], "parse_stats": [], "repairs": []}

    # ---- extraction samples
    n_samples = min(cfg["samples"][budget], cap) if cfg["vote"] else 1
    views = [build_messages(roster, lines, cfg["strip_hedges"], reverse=False, slots=cfg["slots"]),
             build_messages(roster, lines, cfg["strip_hedges"], reverse=True, slots=cfg["slots"])]
    samples = []
    for k in range(n_samples):
        msgs = views[k % 2] if cfg["diverse"] else views[0]
        text = call_model(iid, msgs, cap=cap, max_tokens=3000)
        log["raw"].append(text)
        parsed, stats = parse_output(text, roster, len(lines),
                                     robust=cfg["robust_parse"],
                                     sentences=lines if cfg["ground"] else None,
                                     reground=cfg["reground"])
        log["parse_stats"].append(stats)
        if text is not None:
            samples.append(parsed)
    rules, agree = _vote(samples, len(lines)) if samples else ({}, {})
    log["agreement"] = agree

    if cfg["rescue"] and len(samples) > 1:
        rules, rescued = _rescue(item, samples, rules)
        log["rescued"] = rescued
    answer, diag = solve(item, rules)
    if cfg["core_flip"] and diag["n_solutions"] == 0 and len(diag["core"]) < 3:
        answer, diag, rules, flipped = _core_flip(item, rules, answer, diag)
        log["core_flip"] = flipped

    # ---- solver-guided repair, only with budget left
    rounds = 0
    while calls_left(iid, cap) > 0 and rounds < 3:
        n = diag["n_solutions"]
        if n == 0 and cfg["repair_unsat"]:
            idx, system = diag["core"], VERIFY_SYSTEM
        elif n > 4 and cfg["repair_wide"]:
            idx = [i for i in range(len(lines)) if not rules.get(i)
                   and any(p in lines[i] for p in roster["people"])]
            system = WIDE_SYSTEM
        else:
            break
        if not idx:
            break
        text, changes = _review(iid, cap, system, roster, lines, cleaned, idx,
                                rules, cfg)
        log["repairs"].append({"kind": "unsat" if n == 0 else "wide",
                               "lines": idx, "raw": text,
                               "changes": {i: [_fmt(r) for r in v] for i, v in changes.items()}})
        rounds += 1
        if not changes:
            break
        rules.update(changes)
        answer, diag = solve(item, rules)

    log["rules"] = {i: [_fmt(r) for r in v] for i, v in rules.items() if v}
    log["diag"] = diag
    return answer, log
