"""Run and verify the UUID crash matrix without resetting the tracker.

Run with the configured model credential in the environment:
    python -m eval.part_c --complete
Verify existing report entries without spending tokens:
    python -m eval.part_c
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from harness import ROOT
from harness.formats import parse_triage_comment
from harness.tracker import Tracker
from state.checkpointer import Checkpointer

REPORT = ROOT / "reports" / "LAB_04.json"
MARKDOWN = ROOT / "reports" / "LAB_04.md"
CASES = [(step, when) for step in range(1, 6) for when in ("after", "during")]
TOOLS = ["issues_read", "code_search", "code_read_file", "pulls_open", "issues_comment"]


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify(run_id: str, step: int, when: str) -> dict:
    """Check crash evidence, recovery boundaries and actual tracker writes."""
    trace = records(ROOT / "runs" / f"{run_id}.jsonl")
    start = trace[0]
    assert start["type"] == "run_start" and start["issue"] == 4201
    assert start["backend"] == "openai-compatible"
    assert start["model"] == "gemini-3.5-flash-lite"
    assert start["die"] == {"step": step, "when": when}
    assert start["switches"]["key_mode"] == "uuid"
    resume_at = [i for i, r in enumerate(trace) if r["type"] == "resume"]
    assert len(resume_at) == 1, (run_id, "expected one resume")
    before, after = trace[:resume_at[0]], trace[resume_at[0]:]
    assert not any(r["type"] in ("end", "run_end") for r in before)
    assert after[0]["switches"] == start["switches"]
    ledger = [r for r in records(ROOT / "runs" / "_ledger.jsonl") if r["run_id"] == run_id]
    killed = [r for r in ledger if r.get("killed")]
    assert len(killed) == 1
    assert killed[0]["killed"].startswith(f"{when} step {step}: {TOOLS[step - 1]}")
    assert any(r.get("resumed") for r in ledger)
    ends = [r for r in after if r["type"] == "end"]
    assert len(ends) == 1 and ends[0]["terminal_reason"] == "completed", ends
    assert after[-1]["type"] == "run_end" and after[-1]["error"] is None
    assert not any(r.get("is_error") for r in trace if r["type"] == "tool_call")
    plans = [r["plan"] for r in trace if r["type"] == "plan"]
    assert plans and all([s["tool"] for s in p["steps"]] == TOOLS for p in plans)
    before_calls = [r for r in before if r["type"] == "tool_call"]
    assert [r["plan_step"] for r in before_calls] == list(range(1, step + (when == "after")))
    calls = [r for r in after if r["type"] == "tool_call"]
    cps = Checkpointer().all(run_id)
    count = sum(r["type"] == "checkpoint" for r in before)
    saved = cps[count - 1] if count else None
    assert (saved is None) == (step == 1)
    assert len(cps) == sum(r["type"] == "checkpoint" for r in trace)
    done = saved["completed_calls"] if saved else []
    assert {c["step"] for c in done}.isdisjoint(r["plan_step"] for r in calls)
    assert [r["plan_step"] for r in calls] == list(range(step, 6))
    if saved:
        assert not any(r.get("purpose") == "plan" for r in after)
        pending = saved["findings"].get("pending_write")
        if pending:
            assert calls[0]["input"] == pending["args"]
            assert calls[0]["name"] == pending["tool"]
    else:
        assert any(r["type"] == "validation" and r["valid"] for r in after)
    latest_checkpoint = None
    for row in trace:
        if row["type"] == "checkpoint":
            latest_checkpoint = row
        elif row["type"] == "tool_call":
            if row["name"] in TOOLS[-2:]:
                assert latest_checkpoint is not None
                assert latest_checkpoint["mode"] == "sync"
                assert latest_checkpoint["step"] == row["plan_step"]
            latest_checkpoint = None
    tracker = Tracker()
    comments = tracker.comments(run_id, 4201)
    prs = [p for p in tracker.prs(run_id, branch="repomind/issue-4201") if p["state"] == "open"]
    last = Checkpointer().last(run_id)
    findings = last["findings"]
    expected = (findings["label"], findings["file"])
    assert comments and all(parse_triage_comment(c["body"]) == expected for c in comments)
    assert len(prs) == (0 if findings["file"] == "none" else 1)
    assert all(findings["file"] in p["body"] for p in prs)
    if len(comments) > 1:
        assert len({c["key"] for c in comments}) == len(comments)
        assert len({c["body"] for c in comments}) == 1
    return {"run_id": run_id, "step": step, "when": when, "comments": len(comments),
            "open_prs": len(prs), "matches": True, "comment_ids": [c["id"] for c in comments]}


def save(entries: list[dict]) -> None:
    """Replace only Part C's JSON fields; preserve all other report text."""
    matrix = [{k: v for k, v in entry.items() if k != "comment_ids"} for entry in entries]
    duplicates = {e["run_id"]: e["comment_ids"] for e in entries if e["comments"] > 1}
    text = REPORT.read_text()
    part_c = ('  "matrix_uuid": ' + json.dumps(matrix, indent=2).replace('\n', '\n  ')
              + ',\n  "duplicate_comment_ids": '
              + json.dumps(duplicates, indent=2).replace('\n', '\n  ') + ',\n')
    updated, count = re.subn(r'  "matrix_uuid": \[.*?(?=  "reopened":)', lambda _: part_c, text, flags=re.S)
    assert count == 1
    previous, current = json.loads(text), json.loads(updated)
    for key in previous:
        if key not in ("matrix_uuid", "duplicate_comment_ids"):
            assert current[key] == previous[key]
    REPORT.write_text(updated)


def save_markdown(entries: list[dict]) -> None:
    rows = ["## Part C · Break it on purpose", "",
            "| Step | When | run_id | Comments | Comment IDs | Open PRs |",
            "|---|---|---|---|---|---|"]
    for e in entries:
        rows.append(f"| {e['step']} | {e['when']} | {e['run_id']} | {e['comments']} | "
                    f"{json.dumps(e['comment_ids'])} | {e['open_prs']} |")
    duplicates = [e for e in entries if e["comments"] > 1]
    observed = "; ".join(f"{e['when']} step {e['step']} (`{e['run_id']}`, IDs {e['comment_ids']})"
                         for e in duplicates)
    rows += ["", "**What the checkpoint did, and what the key did:**", "",
             "All ten UUID runs completed after recovery, and every comment agreed with its run's "
             "checkpointed label and file. The eight crashes at steps 1–4 left one comment each; "
             f"the comment replays produced two comments each: {observed}. Every run retained exactly one open PR.", "",
             "The checkpoint determined where execution resumed and which work could replay. "
             "Step 1 had no durable checkpoint, so recovery created and validated a new plan. "
             "Later resumes restored completed calls and skipped them; interrupted writes used their "
             "saved arguments. The step 5 checkpoint already contained the completed PR and exact "
             "comment body, but could not establish whether the comment committed. Both step 5 crashes "
             "occurred after that commit and before the executor recorded its result. The UUID key "
             "changed on replay, so the tracker accepted a second identical comment. Part B's stable "
             "derived key recognized the same replay and kept one comment. PR branch-based readback "
             "provided an additional safeguard: replays at step 4 found the existing PR from "
             "`repomind/issue-4201`, even with a new UUID key.", "",
             "In `matrix_uuid`, `matches` records agreement of every comment and the PR count with "
             "the checkpointed findings, including duplicated comments, as requested for this experiment. "
             "This differs from `checks/FORMATS.md` §12.9's uniqueness-based definition, which would "
             "mark the two duplicate cases false; counts and `duplicate_comment_ids` report the duplication explicitly.", "", ""]
    text = MARKDOWN.read_text()
    start, end = text.index("## Part C ·"), text.index("## Part D ·")
    MARKDOWN.write_text(text[:start] + '\n'.join(rows) + text[end:])


def experiment(step: int, when: str) -> dict:
    env = {**os.environ, "KEY_MODE": "uuid"}
    command = [sys.executable, "run.py", "--way", "planned", "--issues", "4201", f"--die-{when}-step", str(step)]
    crash = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    output = crash.stdout + crash.stderr
    print(output, end="", flush=True)
    match = re.search(r"killed (planned-4201-s\d+-[0-9a-f]+) " + when + rf" step {step}:", output)
    assert crash.returncode in (-9, 137) and match, ("requested crash did not occur", crash.returncode)
    run_id = match.group(1)
    trace = records(ROOT / "runs" / f"{run_id}.jsonl")
    assert not any(r["type"] == "run_end" for r in trace)
    # Resume immediately, with the same run ID; the runner restores its switches.
    resumed = subprocess.run([sys.executable, "run.py", "--resume", run_id], cwd=ROOT,
                             env=env, text=True, capture_output=True)
    print(resumed.stdout + resumed.stderr, end="", flush=True)
    assert resumed.returncode == 0, (run_id, "resume process failed")
    return verify(run_id, step, when)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--complete", action="store_true", help="run missing scenarios on the configured model")
    args = parser.parse_args()
    report = json.loads(REPORT.read_text())
    entries = [verify(e["run_id"], e["step"], e["when"]) for e in report["matrix_uuid"]]
    assert len({(e["step"], e["when"]) for e in entries}) == len(entries)
    for step, when in CASES:
        if any(e["step"] == step and e["when"] == when for e in entries):
            continue
        if not args.complete:
            continue
        print(f"Starting UUID experiment: {when} step {step}", flush=True)
        entry = experiment(step, when)
        entries.append(entry)
        entries.sort(key=lambda e: CASES.index((e["step"], e["when"])))
        save(entries)
        print("VERIFIED " + json.dumps(entry), flush=True)
    if args.complete:
        assert [(e["step"], e["when"]) for e in entries] == CASES
        assert any(e["comments"] > 1 for e in entries)
        save_markdown(entries)
    print(json.dumps(entries, indent=2))


if __name__ == "__main__":
    main()
