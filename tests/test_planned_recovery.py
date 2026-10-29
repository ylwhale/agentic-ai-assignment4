"""Planner argument validation and crash recovery, using isolated scripted runs."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.plan import make_plan
from harness import ROOT
from harness.client import ModelClient
from harness.formats import parse_triage_comment
from harness.pricing import PriceSheet
from harness.runner import WayContext
from harness.scripted import OffPlanExecutor, RecordingPlanner, ScriptedTriage
from harness.settings import load_settings
from harness.trace import TraceWriter, read_trace
from harness.tracker import Tracker
from state.checkpointer import Checkpointer
from tools import RunContext, default_tools
from ways import planned


class SimulatedCrash(BaseException):
    """Stop at the real harness kill hook without killing the test process."""


class PlannedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.checkpointer = Checkpointer(self.root / "checkpoints.db")
        self.patch = patch.object(planned, "Checkpointer", side_effect=lambda: self.checkpointer)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def context(self, backend=None, *, die=None, resume=False, mode="derived"):
        tracker = Tracker(ROOT, state_dir=self.root / "tracker", flaky=False, readback_fail=False)
        tools = default_tools(RunContext(
            "test-run", tracker, ROOT / "fixtures" / "repo", switches={"key_mode": mode}
        ))
        trace = TraceWriter(self.root / "trace.jsonl", "test-run", {}, resume=resume)
        self.addCleanup(trace.close)
        return WayContext(
            "test-run", 4201, ModelClient(backend or ScriptedTriage(), PriceSheet()),
            tools, trace, tracker, load_settings(), ROOT, die=die
        )

    def plan(self, ctx):
        return make_plan(ctx.client, ctx.issue_number, ctx.tools.schemas)

    def test_an_ordinary_five_step_plan_passes(self):
        ctx = self.context()
        plan = self.plan(ctx)
        self.assertEqual(len(plan["steps"]), 5)
        self.assertEqual(planned.schema_check(ctx, plan, 1), [])

    def test_unknown_and_missing_argument_names_are_rejected(self):
        ctx = self.context()
        plan = self.plan(ctx)
        args = json.loads(plan["steps"][-1]["args_json"])
        args["<body>"] = args.pop("body")
        plan["steps"][-1]["args_json"] = json.dumps(args)
        errors = str(planned.schema_check(ctx, plan, 1))
        self.assertIn("<body>", errors)
        self.assertIn("body: required, and missing", errors)

    def test_nullable_required_arguments_cannot_be_omitted(self):
        ctx = self.context()
        plan = self.plan(ctx)
        args = json.loads(plan["steps"][1]["args_json"])
        self.assertIsNone(args.pop("path_glob"))
        plan["steps"][1]["args_json"] = json.dumps(args)
        self.assertIn("path_glob: required, and missing", str(planned.schema_check(ctx, plan, 1)))

    def test_value_types_and_enums_are_checked_but_fills_are_deferred(self):
        ctx = self.context()
        plan = self.plan(ctx)
        for value in (None, 12, "verbose", "$fill", "$fill: choose a format"):
            with self.subTest(value=value):
                args = json.loads(plan["steps"][1]["args_json"])
                args["response_format"] = value
                plan["steps"][1]["args_json"] = json.dumps(args)
                problems = planned.schema_check(ctx, plan, 1)
                self.assertEqual(bool(problems), not isinstance(value, str) or not value.startswith("$fill"))

    def test_malformed_and_non_object_json_are_rejected(self):
        ctx = self.context()
        plan = self.plan(ctx)
        for raw in ('{"query": "$fill",', r'{\"query\": \"$fill\"}', '[]', 'null', '{}'):
            with self.subTest(raw=raw):
                plan["steps"][1]["args_json"] = raw
                self.assertTrue(planned.schema_check(ctx, plan, 1))

    def test_schema_failure_replans_and_exhaustion_escalates(self):
        ctx = self.context()
        good = self.plan(ctx)
        bad = copy.deepcopy(good)
        bad["steps"][1]["args_json"] = '{"query":'
        with patch.object(planned, "make_plan", side_effect=[bad, good]) as make:
            self.assertEqual(planned.plan_until_valid(ctx), good)
            self.assertIn("invalid args_json", str(make.call_args.kwargs["failures"]))
        with patch.object(planned, "make_plan", return_value=bad) as make:
            self.assertIsNone(planned.plan_until_valid(ctx))
            self.assertEqual(make.call_count, planned.MAX_REPLANS + 1)
        self.assertEqual(read_trace(ctx.trace.path)[-1]["terminal_reason"], "escalated")

    def test_planner_receives_complete_argument_schemas_and_encoding_feedback(self):
        backend = RecordingPlanner()
        ctx = self.context(backend)
        make_plan(ctx.client, 4201, ctx.tools.schemas, failures=[{"check": "schema"}])
        request = backend.requests[-1]
        user = request["messages"][0]["content"]
        for schema in ctx.tools.schemas:
            self.assertIn(json.dumps(schema["input_schema"]), user)
        self.assertIn("json.loads(step['args_json'])", user)
        self.assertIn('"body": "$fill"', request["system"])

    def test_off_plan_calls_never_reach_the_registry(self):
        ctx = self.context(OffPlanExecutor())
        planned.run(ctx)
        records = read_trace(ctx.trace.path)
        calls = [r for r in records if r["type"] == "tool_call"]
        self.assertEqual([r["plan_step"] for r in calls], [1])
        self.assertEqual(len([r for r in records if r["type"] == "off_plan"]), 2)
        self.assertEqual(records[-1]["terminal_reason"], "escalated")
        self.assertEqual(ctx.tracker.comments(ctx.run_id), [])

    def test_filled_argument_values_must_fit_the_tool_schema(self):
        class InvalidFormat(ScriptedTriage):
            def create(self, **kwargs):
                content, stop, model, usage = super().create(**kwargs)
                content[0]["input"]["response_format"] = 17
                return content, stop, model, usage

        ctx = self.context()
        plan = self.plan(ctx)
        ctx.client = ModelClient(InvalidFormat(), PriceSheet())
        executor = planned.Executor(ctx, plan)
        args = json.loads(plan["steps"][1]["args_json"])
        args["response_format"] = "$fill"
        self.assertIsNone(executor.filled(plan["steps"][1], args))

    def test_all_crash_boundaries_preserve_completed_calls_and_pending_arguments(self):
        for mode in ("derived", "uuid"):
            for step in range(1, 6):
                for when in ("after", "during"):
                    with self.subTest(mode=mode, step=step, when=when), tempfile.TemporaryDirectory() as tmp:
                        self.root = Path(tmp)
                        self.checkpointer = Checkpointer(self.root / "checkpoints.db")
                        ctx = self.context(die=(when, step), mode=mode)

                        def crash(detail):
                            raise SimulatedCrash(detail)

                        ctx.on_die = crash
                        with self.assertRaises(SimulatedCrash):
                            planned.run(ctx)
                        ctx.trace.close()
                        saved = self.checkpointer.last(ctx.run_id)
                        self.assertEqual(saved is None, step == 1)
                        resumed = self.context(resume=True, mode=mode)
                        planned.run(resumed)
                        records = read_trace(resumed.trace.path)
                        at = next(i for i, r in enumerate(records) if r["type"] == "resume")
                        calls = [r for r in records[at:] if r["type"] == "tool_call"]
                        self.assertTrue(all(not r["is_error"] for r in calls))
                        done = saved["completed_calls"] if saved else []
                        self.assertTrue({c["step"] for c in done}.isdisjoint(c["plan_step"] for c in calls))
                        if saved and saved["findings"].get("pending_write"):
                            pending = saved["findings"]["pending_write"]
                            self.assertEqual(calls[0]["plan_step"], pending["step"])
                            self.assertEqual(calls[0]["input"], pending["args"])
                        comments = resumed.tracker.comments(ctx.run_id, 4201)
                        self.assertEqual(len(comments), 2 if mode == "uuid" and step == 5 else 1)
                        self.assertEqual(len(resumed.tracker.prs(ctx.run_id, branch="repomind/issue-4201")), 1)
                        findings = self.checkpointer.last(ctx.run_id)["findings"]
                        self.assertTrue(all(parse_triage_comment(c["body"]) == (findings["label"], findings["file"])
                                            for c in comments))
                        writes = [r for r in calls if r["name"] in ("pulls_open", "issues_comment")]
                        self.assertEqual(writes[-1]["name"], "issues_comment")
                        if step == 1:
                            self.assertEqual(len([r for r in records[at:] if r.get("purpose") == "plan"]), 1)
                        else:
                            self.assertFalse(any(r.get("purpose") == "plan" for r in records[at:]))


if __name__ == "__main__":
    unittest.main()
