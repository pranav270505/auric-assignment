"""
Unit tests for the deterministic parts of the system (no network, no model).

    python3 -m unittest discover -s tests -v
"""
import itertools
import json
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import client  # noqa: E402
import pipeline  # noqa: E402
import score  # noqa: E402
from extract import (clean_line, grounded, parse_output,  # noqa: E402
                     regrounded)
from solver import (body_lines, deletion_conflict, enumerate_schedules,  # noqa: E402
                    line_masks, parse_header, parse_rule, smallest_conflict,
                    solve)

HEADER = ("Shift notes, Bay 1. 5 staff on the rota: Ana, Ben, Cara, Dev, Eva. "
          "Blocks run 07:00, 09:00, 11:00, 13:00, 15:00, one person per block, and "
          "each person works exactly one block. There are 3 stations, one person on "
          "each: intake, calibration, packing. The people on a station are Ana, Ben, "
          "Cara; the rest are on no station.")


def load(path):
    with open(path) as f:
        return json.load(f)


def item(lines, iid="T-000"):
    return {"id": iid, "text": HEADER + "\n\n" + "\n".join(lines)}


ROSTER = parse_header(HEADER)


def rules(*strs):
    return [parse_rule(s, ROSTER) for s in strs]


class TestHeader(unittest.TestCase):
    def test_header(self):
        self.assertEqual(ROSTER["people"], ["Ana", "Ben", "Cara", "Dev", "Eva"])
        self.assertEqual(ROSTER["blocks"], ["07:00", "09:00", "11:00", "13:00", "15:00"])
        self.assertEqual(ROSTER["stations"], ["intake", "calibration", "packing"])
        self.assertEqual(ROSTER["holders"], ["Ana", "Ben", "Cara"])

    def test_every_visible_header_parses(self):
        for it in load("items.json"):
            r = parse_header(it["text"])
            self.assertEqual((len(r["people"]), len(r["blocks"]), len(r["stations"])),
                             (5, 5, 3), it["id"])

    def test_schedule_count(self):
        self.assertEqual(len(enumerate_schedules(ROSTER)), 720)


class TestRuleParsing(unittest.TestCase):
    def test_valid_and_normalised(self):
        self.assertEqual(parse_rule("before(ana, @Packing)", ROSTER), ("before", "Ana", "@packing"))
        self.assertEqual(parse_rule("at(Dev, 9:00)", ROSTER), ("at", "Dev", "09:00"))
        self.assertEqual(parse_rule("noise", ROSTER), "noise")
        # between endpoints are unordered, so they are sorted
        self.assertEqual(parse_rule("between(Eva, Dev, Ana)", ROSTER),
                         parse_rule("between(Eva, Ana, Dev)", ROSTER))

    def test_invalid(self):
        for s in ["at(Zed, 09:00)", "at(Ana, 10:00)", "on(Ana, welding)",
                  "before(Ana, Ana)", "between(Ana, Ben)", "frobnicate(Ana)", ""]:
            self.assertIsNone(parse_rule(s, ROSTER), s)


class TestSlotVariant(unittest.TestCase):
    def test_named_slots_decide_order(self):
        self.assertEqual(parse_rule("before(later=Ben, earlier=Ana)", ROSTER), ("before", "Ana", "Ben"))
        self.assertEqual(parse_rule("right_before(earlier=@intake, later=Dev)", ROSTER),
                         ("right_before", "@intake", "Dev"))
        self.assertEqual(parse_rule("between(Ana, middle=Cara, Ben)", ROSTER),
                         parse_rule("between(Cara, Ana, Ben)", ROSTER))
        self.assertIsNone(parse_rule("before(first=Ana, later=Ben)", ROSTER))

    def test_pipeline_defaults(self):
        self.assertFalse(pipeline.DEFAULT_CONFIG["slots"])
        self.assertTrue(pipeline.DEFAULT_CONFIG["core_flip"])

    def test_core_flip_repairs_two_line_clash_only(self):
        # Ana at 07:00 and before(Ben, Ana) clash on 2 lines -> reversal fixes it
        lines = ["a", "b", "c", "d", "e", "f", "g"]
        lr = {0: rules("at(Ana, 07:00)"), 1: rules("before(Ben, Ana)"),
              2: rules("at(Ben, 09:00)"), 3: rules("at(Cara, 11:00)"), 4: rules("at(Dev, 13:00)"),
              5: rules("on(Ana, intake)"), 6: rules("on(Ben, calibration)")}
        it = item(lines)
        a, d = solve(it, lr)
        self.assertEqual(len(d["core"]), 2)
        a2, d2, _, flipped = pipeline._core_flip(it, lr, a, d)
        self.assertIsNotNone(flipped)
        self.assertIn(a2["case"], ("unique", "ambiguous"))
        # a 3-line contradiction is never touched by the pipeline (size gate)
        lr3 = {0: rules("at(Ana, 07:00)"), 1: rules("right_before(Ana, Ben)"),
               2: rules("before(Cara, Ben)")}
        self.assertEqual(len(solve(item(["x", "y", "z"]), lr3)[1]["core"]), 3)

    def test_default_prompt_is_positional(self):
        from extract import SYSTEM, SYSTEM_SLOTS, build_messages
        self.assertEqual(build_messages(ROSTER, ["x"])[0]["content"], SYSTEM)
        self.assertNotIn("earlier=", SYSTEM)
        self.assertIn("earlier=", build_messages(ROSTER, ["x"], slots=True)[0]["content"])


class TestSolver(unittest.TestCase):
    def test_unique(self):
        it = item(["l"] * 9)
        lr = {i: [r] for i, r in enumerate(rules(
            "at(Ana, 07:00)", "at(Ben, 09:00)", "at(Cara, 11:00)", "at(Dev, 13:00)",
            "on(Ana, intake)", "on(Ben, calibration)"))}
        ans, d = solve(it, lr)
        self.assertEqual(ans["case"], "unique")
        self.assertEqual(ans["assignment"]["Eva"], {"block": "15:00"})
        self.assertEqual(ans["assignment"]["Cara"], {"block": "11:00", "station": "packing"})

    def test_ambiguous_returns_all(self):
        it = item(["l"] * 5)
        lr = {i: [r] for i, r in enumerate(rules(
            "at(Ana, 07:00)", "at(Ben, 09:00)", "at(Cara, 11:00)",
            "on(Ana, intake)", "on(Ben, calibration)"))}
        ans, d = solve(it, lr)
        self.assertEqual(ans["case"], "ambiguous")
        self.assertEqual(len(ans["assignments"]), 2)       # Dev/Eva swap 13:00/15:00

    def test_inconsistent_cites_verbatim_minimal_lines(self):
        lines = ["Ana is first.", "The kettle broke.", "Ben is right after Ana.",
                 "Cara is earlier than Ben.", "Dev is not at 15:00."]
        lr = {0: rules("at(Ana, 07:00)"), 2: rules("right_before(Ana, Ben)"),
              3: rules("before(Cara, Ben)"), 4: rules("not_at(Dev, 15:00)")}
        ans, d = solve(item(lines), lr)
        self.assertEqual(ans["case"], "inconsistent")
        self.assertEqual(sorted(ans["conflicts"]), sorted([lines[0], lines[2], lines[3]]))

    def test_station_terms(self):
        # whoever holds intake is earlier than Dev, Dev is at 07:00 -> impossible
        lr = {0: rules("at(Dev, 07:00)"), 1: rules("before(@intake, Dev)")}
        ans, _ = solve(item(["a", "b"]), lr)
        self.assertEqual(ans["case"], "inconsistent")

    def test_conflict_search_is_minimal_on_random_sets(self):
        rng = random.Random(1)
        sch = enumerate_schedules(ROSTER)
        pool = [r for r in (parse_rule(f"{op}({a}, {b})", ROSTER)
                            for op in ("before", "right_before")
                            for a, b in itertools.permutations(ROSTER["people"], 2))]
        pool += [parse_rule(f"at({p}, {t})", ROSTER) for p in ROSTER["people"]
                 for t in ROSTER["blocks"]]
        tested = 0
        while tested < 25:
            lr = {i: [r] for i, r in enumerate(rng.sample(pool, 7))}
            masks, full = line_masks(lr, sch, ROSTER)
            m = full
            for v in masks.values():
                m &= v
            if m:
                continue
            for core in (smallest_conflict(masks, full), deletion_conflict(masks, full)):
                inter = full
                for i in core:
                    inter &= masks[i]
                self.assertEqual(inter, 0, "core must be unsatisfiable")
                for drop in core:
                    sub = full
                    for i in core:
                        if i != drop:
                            sub &= masks[i]
                    self.assertNotEqual(sub, 0, "dropping any line must make it satisfiable")
            tested += 1


class TestHedgesAndParsing(unittest.TestCase):
    def test_clean_line(self):
        self.assertEqual(clean_line("It bears repeating: I'm fairly sure Ana is on intake."),
                         "Ana is on intake.")
        self.assertEqual(clean_line("As far as I know, the 09:00 block is Ben's."),
                         "The 09:00 block is Ben's.")
        # an unknown hedge is left for the model, never mangled
        self.assertEqual(clean_line("Word on the floor is Ana is on intake."),
                         "Word on the floor is Ana is on intake.")
        # a head containing a time is not a restatement prefix
        self.assertEqual(clean_line("At 11:00: Dev takes over."), "At 11:00: Dev takes over.")

    def test_robust_parser(self):
        raw = ("Here you go:\n"
               "L1: before(@packing, Ben)  // because packing is earlier\n"
               "L2: noise\n"
               "L3: at(Ana, packing)\n"                  # station in a block slot
               "L4: before(Dev, Eva)\n"
               "L4: before(Eva, Dev)\n")                 # corrected later: last wins
        out, _ = parse_output(raw, ROSTER, 5)
        self.assertEqual(out[0], [("before", "@packing", "Ben")])
        self.assertEqual(out[1], [])
        self.assertEqual(out[2], [("on", "Ana", "packing")])
        self.assertEqual(out[3], [("before", "Eva", "Dev")])
        self.assertNotIn(4, out)                         # missing line = noise

    def test_grounding_and_regrounding(self):
        s = "Cara relieves Ben directly."
        self.assertTrue(grounded(("right_before", "Ben", "Cara"), s))
        self.assertFalse(grounded(("right_before", "Ben", "Dev"), s))
        # one copied name, one unused name in the sentence -> repaired
        self.assertEqual(regrounded(("right_before", "Ben", "Dev"), s, ROSTER),
                         ("right_before", "Ben", "Cara"))
        # two foreign names -> dropped
        self.assertIsNone(regrounded(("right_before", "Ana", "Dev"), s, ROSTER))


class FakeOpenAI:
    """Stands in for the OpenAI client: fails n_fail times, then answers."""
    def __init__(self, answer, n_fail=0):
        self.answer, self.n_fail, self.seen = answer, n_fail, []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.seen.append(kw)
        if self.n_fail:
            self.n_fail -= 1
            raise RuntimeError("boom")
        msg = type("M", (), {"content": self.answer(kw["messages"])})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})


class TestClientAndBudget(unittest.TestCase):
    def setUp(self):
        client.CALLS.clear()
        self._old = client._client
        client.time.sleep = lambda s: None

    def tearDown(self):
        client._client = self._old

    def test_required_settings_and_header(self):
        client._client = FakeOpenAI(lambda m: "L1: noise")
        client.call_model("X-1", [{"role": "user", "content": "hi"}], cap=1)
        kw = client._client.seen[0]
        self.assertEqual(kw["extra_headers"], {"X-Item-Id": "X-1"})
        self.assertEqual((kw["temperature"], kw["top_p"]), (1.0, 0.95))
        self.assertEqual(kw["extra_body"], {"reasoning": {"enabled": False}})

    def test_retries_never_exceed_cap(self):
        client._client = FakeOpenAI(lambda m: "x", n_fail=10)
        self.assertIsNone(client.call_model("X-2", [], cap=3))
        self.assertEqual(client.CALLS["X-2"], 3)

    def test_pipeline_uses_exact_budget_and_model_output_matters(self):
        it = load("items.json")[2]
        n = len(body_lines(it["text"]))
        answers = {}
        for name, reply in [("all noise", lambda m: "\n".join(f"L{i}: noise" for i in range(1, n + 1))),
                            ("one rule", lambda m: "L3: right_before(Meera, Rohan)")]:
            for budget, cap in (("1x", 1), ("3x", 3), ("10x", 10)):
                client.CALLS.clear()
                client._client = FakeOpenAI(reply)
                ans, _ = pipeline.run_item(it, budget)
                self.assertEqual(client.CALLS[it["id"]], cap, (name, budget))
            answers[name] = ans
        # the answer must change when the model's output changes
        self.assertNotEqual(answers["all noise"], answers["one rule"])


class TestGeneratedSets(unittest.TestCase):
    def test_fresh_set_gold_rules_score_100(self):
        d = os.path.join("dev", "fresh")
        if not os.path.exists(os.path.join(d, "items.json")):
            self.skipTest("run dev/make_fresh_set.py first")
        items = load(os.path.join(d, "items.json"))
        key = load(os.path.join(d, "key.json"))
        gold = load(os.path.join(d, "gold_rules.json"))
        ans = {}
        for it in items:
            ro = parse_header(it["text"])
            lr = {int(i): [parse_rule(r, ro) for r in rs] for i, rs in gold[it["id"]].items()}
            ans[it["id"]], _ = solve(it, lr)
        self.assertEqual(score.score(key, ans, items)["macro_exact_match"], 1.0)


if __name__ == "__main__":
    unittest.main()
