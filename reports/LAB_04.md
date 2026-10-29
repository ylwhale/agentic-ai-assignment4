# Assignment 4 · Kill it and resume: state, keys and a memory with provenance

Model: Google Gemini `gemini-3.5-flash-lite`, using the OpenAI-compatible API. The same model will be used throughout the model-based experiments. The initial Part A plan inspection used the scripted backend.

## Part A · Mark the plan, place the checkpoints

| Step | Tool | Kind | Checkpoint |
|---|---|---|---|
| 1 | `issues_read` | read | after |
| 2 | `code_search` | read | after |
| 3 | `code_read_file` | read | after |
| 4 | `pulls_open` | keyed_write | before |
| 5 | `issues_comment` | keyed_write | before |

**Durability mode, and why:**

I chose `sync` durability because RepoMind performs irreversible write operations when opening pull requests and posting comments. A synchronous checkpoint ensures that execution state is saved to persistent storage before a write begins. This makes it possible to recover the validated plan, completed calls, findings, and exact write arguments after a crash.

I placed checkpoints after the three read operations to avoid repeating completed work, and immediately before the two writes to minimize the replay window. However, checkpointing alone does not prevent duplicate writes; idempotency keys are still necessary for safe retries.

**The weaker of the two writes, and why:**

`issues_comment` is the weaker write because it relies on its idempotency key to prevent duplication. If the key changes during a retry, the tracker may treat the comment as a new write and create a duplicate.

In contrast, `pulls_open` has an additional safeguard: RepoMind can check for an existing pull request using the fixed branch name `repomind/issue-4201`. Therefore, even if its idempotency key changes, the branch check can prevent a duplicate PR.

## Part B · Kill it at every step

**The hand walk, written before the matrix.**

**The crash I expect to be worst:**

I predict that crashing during step 5 (`issues_comment`) will be the most dangerous scenario. Specifically, I will test `--die-during-step 5`, which kills RepoMind after the tracker commits the comment but before the tool returns its acknowledgement.

**Its last checkpoint:**

The most recent checkpoint should be the synchronous checkpoint saved immediately before step 5. It should contain the validated plan, the completed calls from steps 1–4, the findings, and the exact arguments prepared for posting the comment.

**The steps between it and the crash, and the step running at the crash:**

No other completed plan steps should exist between the last checkpoint and the crash. Steps 1–4 should already be completed, including opening the draft pull request in step 4.

Step 5 is the running step when the crash occurs. The tracker has committed the comment, but RepoMind has not received confirmation of the result.

**For each: its side effect, and what closes it (a key, a recorded result, or nothing):**

- Steps 1–3 are read operations without irreversible side effects. Their completed results should already be recorded in checkpoints, so they can be skipped during recovery.
- Step 4 opens a draft pull request, which is an irreversible side effect. Its completed result should already be recorded in the last checkpoint. The existing branch check also provides protection against duplicate PR creation.
- Step 5 posts a triage comment, which is an irreversible side effect. Its outcome is unknown to RepoMind when the process crashes. The resumed run should retry this operation with the exact arguments saved in the checkpoint and the same derived idempotency key. The tracker should recognize that the write has already occurred and prevent another comment from being created.

**Predicted outcome:**

After recovery, I expect exactly one triage comment and one open draft pull request for issue 4201. The final writes should match the run's checkpointed findings, with no duplicated side effects.

This prediction assumes that the checkpointer correctly saves the pending write arguments and that the resumed process reuses them rather than generating new arguments.

**The matrix, derived keys.** Uncrashed run: `planned-4201-s01-f62e39`

| Step | When | run_id | Comments | Open PRs | Matches its findings |
|---|---|---|---|---|---|
| 1 | after | planned-4201-s01-0d8c86 | 1 | 1 | Yes |
| 1 | during | planned-4201-s01-ea38e9 | 1 | 1 | Yes |
| 2 | after | planned-4201-s01-41d797 | 1 | 1 | Yes |
| 2 | during | planned-4201-s01-8e64c6 | 1 | 1 | Yes |
| 3 | after | planned-4201-s01-44cbc2 | 1 | 1 | Yes |
| 3 | during | planned-4201-s01-1110ca | 1 | 1 | Yes |
| 4 | after | planned-4201-s01-cc99b8 | 1 | 1 | Yes |
| 4 | during | planned-4201-s01-06837d | 1 | 1 | Yes |
| 5 | after | planned-4201-s01-49cc70 | 1 | 1 | Yes |
| 5 | during | planned-4201-s01-edaa9b | 1 | 1 | Yes |

## Part C · Break it on purpose

| Step | When | run_id | Comments | Comment IDs | Open PRs |
|---|---|---|---|---|---|
| 1 | after | planned-4201-s01-061c49 | 1 | [1] | 1 |
| 1 | during | planned-4201-s01-f5e617 | 1 | [2] | 1 |
| 2 | after | planned-4201-s01-200384 | 1 | [3] | 1 |
| 2 | during | planned-4201-s01-037409 | 1 | [4] | 1 |
| 3 | after | planned-4201-s01-a77637 | 1 | [5] | 1 |
| 3 | during | planned-4201-s01-f72be3 | 1 | [6] | 1 |
| 4 | after | planned-4201-s01-ce8a1c | 1 | [7] | 1 |
| 4 | during | planned-4201-s01-7d54c4 | 1 | [8] | 1 |
| 5 | after | planned-4201-s01-3bdf47 | 2 | [9, 10] | 1 |
| 5 | during | planned-4201-s01-df423c | 2 | [11, 12] | 1 |

**What the checkpoint did, and what the key did:**

All ten UUID runs completed after recovery, and every comment agreed with its run's checkpointed label and file. The eight crashes at steps 1–4 left one comment each; the comment replays produced two comments each: after step 5 (`planned-4201-s01-3bdf47`, IDs [9, 10]); during step 5 (`planned-4201-s01-df423c`, IDs [11, 12]). Every run retained exactly one open PR.

The checkpoint determined where execution resumed and which work could replay. Step 1 had no durable checkpoint, so recovery created and validated a new plan. Later resumes restored completed calls and skipped them; interrupted writes used their saved arguments. The step 5 checkpoint already contained the completed PR and exact comment body, but could not establish whether the comment committed. Both step 5 crashes occurred after that commit and before the executor recorded its result. The UUID key changed on replay, so the tracker accepted a second identical comment. Part B's stable derived key recognized the same replay and kept one comment. PR branch-based readback provided an additional safeguard: replays at step 4 found the existing PR from `repomind/issue-4201`, even with a new UUID key.

In `matrix_uuid`, `matches` records agreement of every comment and the PR count with the checkpointed findings, including duplicated comments, as requested for this experiment. This differs from `checks/FORMATS.md` §12.9's uniqueness-based definition, which would mark the two duplicate cases false; counts and `duplicate_comment_ids` report the duplication explicitly.

## Part D · A memory that can say where it came from

**The write policy, in brief:**

RepoMind writes reusable knowledge after successful triage or independent verification, rather than promoting an unverified intermediate hypothesis. It stores useful prior events as episodic memory, verified repository facts as semantic memory, and reusable procedures as procedural memory.

Each record retains its repository and issue scope, source, timestamp, origin, and confirmation status. The source distinguishes maintainer statements, issue-body claims, repository evidence, and RepoMind's own previous work.

Equivalent records should be consolidated. When authoritative new evidence contradicts an existing rule, the old record becomes inactive and a replacement record identifies it through `supersedes`. An unverified issue-body claim must never override a confirmed maintainer instruction.

The full policy is documented in `state/write_policy.md`.

**The three candidates, and how each was stored:**

| Candidate | Stored? | Kind | source | origin | confirmed | Invites |
|---|---|---|---|---|---|---|
| A — Derived triage procedure for issue 4191 | Yes | procedural | `memory` | derived | true | Staleness if repository commands or configuration change |
| B — Auto-merge claim from issue 4188 | Yes, as an untrusted claim | episodic | `issue:4188` | ingested | false | Memory poisoning if promoted to authoritative policy |
| C — Maintainer `priya-s`'s duplicate-handling rule | Yes | procedural | `user` | ingested | true | Contradiction when the maintainer changes the rule |

Candidate A is a procedure derived from previous work and recorded as confirmed. It must be revalidated when relevant repository configuration changes. Candidate B records what the issue author claimed without establishing that auto-merging is allowed; it is excluded from authoritative memory context. Candidate C is a repository-wide rule attributed to an identified maintainer, so it is confirmed as a stated instruction, not as an immutable fact.

**Eviction: the size, the rule, and how long an entry lives at the assumed write rate:**

The SQLite store permits up to **250 active records per repository**. Equivalent records are consolidated, superseded rules become inactive, and capacity eviction prioritizes low-confidence episodic observations. Derived procedures should be revisited when their supporting repository configurations change; the policy identifies 90 days without reconfirmation as a review priority. Confirmed maintainer rules are preserved until superseded or revoked.

The implementation normally writes one episodic triage record per successfully completed run. Assuming **20 successful runs per day**, each producing one distinct retained record, a previously empty 250-record store would reach capacity in approximately **12.5 days**. This is a capacity estimate, not a guaranteed lifetime: low-confidence entries may be evicted, while confirmed rules may remain much longer.

**Issue 4207: Five runs with memory versus five runs with an empty store**

| Metric | With the store | Empty store |
|---|---:|---:|
| Passes | 5/5 | 5/5 |
| Mean tool calls | 5 | 5 |
| Mean tokens | 13,223 | 13,630.4 |

Both conditions achieved 100% grounded triage success. With memory, the average token count was approximately 3.0% lower, but tool calls and correctness were unchanged. With only five runs per condition, these results do not establish a reliable performance advantage. The clearer benefit of the store is the ability to retrieve provenance-tagged information across runs, subject to its trust and supersession policy.

## Part E · Answer from your own runs

### 1. Your prediction against the matrix

I predicted that a crash during step 5 (`issues_comment`) would be the most dangerous because the tracker might commit the comment before RepoMind received its acknowledgement. The last synchronous checkpoint would contain the completed earlier steps and the exact pending comment arguments, but it could not establish whether the comment had already been committed.

The Part B derived-key matrix supported this prediction. All ten crash scenarios resumed successfully, and every run ended with exactly one triage comment and one open pull request matching its checkpointed findings. In particular, both step 5 crash scenarios recovered without creating duplicate comments.

The limitation in my prediction was its dependence on a stable idempotency key. Part C demonstrated why that assumption matters: when I replaced derived keys with fresh UUID keys, the two step 5 replay scenarios each created two comments. The checkpoint bounded the work that replayed, but only stable keys prevented duplicate writes.

### 2. The contradiction test

I tested whether persistent memory correctly replaces an outdated maintainer rule across three separate sessions.

In the original session, Memory ID 3 contained maintainer `priya-s`'s confirmed procedural rule: label duplicate reports and link the original, but do not close duplicates automatically.

In the update session, I introduced a replacement rule directing duplicates to be closed after 14 days. `Store.supersede()` marked record 3 inactive and created record 11 with `supersedes = 3`.

In the third session, I queried active memories again. Among the duplicate-handling rules, the active result contained only record **11**, not the superseded record 3. The experiment therefore demonstrated that the store preserves history without presenting contradictory versions of the same rule as simultaneously current.

The report records the retrieved replacement ID as `contradiction_test: [11]`. This was a controlled policy-change experiment rather than a real production policy update.

### 3. What your checkpoint cost

I measured five checkpoints from run `planned-4191-s01-01585f`.

The structured checkpoints occupied **28,933 bytes** in total. Copies augmented with the model conversation occupied **134,981 bytes**.

| Measurement | Bytes |
|---|---:|
| Structured checkpoints | 28,933 |
| Checkpoints including conversation | 134,981 |
| Storage multiplier | 4.67x |
| Runs measured | 1 |
| Checkpoints measured | 5 |

Including the conversation increased checkpoint storage by approximately 106,048 bytes, or 367%.

The experiment used a separate file, `reports/checkpoint_conversation_planned-4191-s01-01585f.jsonl`, for the conversation-augmented copies. The actual checkpoint database retained only structured execution state.

This supports the design choice to checkpoint the validated plan, progress, completed calls, findings, and pending write arguments rather than the complete model conversation. The measurement covers one run, so it should not be treated as a population-wide storage estimate.

### 4. Is what you built durable execution?

RepoMind implements important recovery mechanisms, but it is **not yet a fully automated durable-execution system**.

First, it has a durable checkpoint: `state/checkpointer.py` saves structured execution state in SQLite with synchronous commits.

Second, it has a completed-call journal: the checkpoint records previously completed tool calls and their results. On resume, the executor restores this information, skips completed calls, and reuses saved arguments for unfinished writes. Stable idempotency keys protect side effects whose acknowledgements were lost.

Third, it does **not** have an automatic supervisor. The system does not independently detect a terminated process and restart the unfinished run. Recovery requires an explicit command such as `python run.py --resume <run_id>`.

Therefore, the implementation is a checkpoint-based recovery system with a completed-call journal and safe replay, rather than a complete durable-execution engine with automatic supervision.

To achieve automatic recovery, I would add a supervisor that identifies unfinished runs, restarts them after failures, and coordinates retries using the existing checkpoints and idempotency mechanisms.

### 5. Which orchestration abstraction?

The lecture's four questions lead to the following design choices for RepoMind.

**Question 1: Do we know the steps and their order in advance?**

Yes. RepoMind follows a validated plan: read the issue, search the repository, inspect a file, open a pull request when appropriate, and post a triage comment. This naturally fits a graph-like workflow with ordered steps rather than unrestricted agent handoffs.

**Question 2: Do runs outlive one process?**

Yes. A run may be interrupted and resumed later. Therefore, execution state must be checkpointed to durable storage rather than kept only in memory.

**Question 3: Do steps have irreversible side effects?**

Yes. Opening a pull request and posting a comment change the tracker. Both operations require idempotency protection, saved arguments, and safe replay. A durable-execution supervisor would be appropriate if unfinished runs needed to finish automatically.

**Question 4: Do we want to write the loop and compaction code ourselves?**

For this assignment, yes. RepoMind uses its own harness and execution logic, with explicit control over prompts, tool boundaries, checkpoints, memory retrieval, and the compaction experiments.

The resulting architecture is a **custom, graph-like planned executor with durable checkpoints, a completed-call journal, idempotent writes, and persistent memory**. It is not a fully supervised durable-execution platform.

No framework automatically decides the application's memory policy or context strategy. I still have to choose what belongs in a checkpoint, which memories are trustworthy and worth retaining, and which information must survive context compaction.

## Part F · Find the boundary that lost it

**The experimental fact, word for word:**

"Ledgerline maintainers classify reports that the Export button stays disabled after an export failure as feature."

This is a simulated maintainer instruction for development issue 4192, not a verified production policy. Its expected effect is `label: feature` in the triage comment.

I tested three distinct forgetting boundaries with the same fact: attention, compaction, and the session boundary. Each experiment used the planned executor, and its outcome was checked against the actual model-request records, execution traces, and tracker comments.

| Case | The lost run | Where it lost the fact (trace and request record) | The fix | The fixed run |
|---|---|---|---|---|
| Attention | `planned-4192-s01-bbb35b` | Fact present in drafting request 5 at 52.5%, but final label was `bug` | Move the same fact to a prominent position near the end of the request | `planned-4192-s01-4c40ce` |
| Compaction | `planned-4192-s01-696322` | Fact present in request 2, removed at compaction (trace line 14), and absent from drafting request 5; final label was `question` | Preserve the fact in a structured note outside compacted history | `planned-4192-s01-bcbfda` |
| The session | `planned-4192-s01-832947` | Empty-store run retrieved no memories and no request contained the fact; final label was `bug` | Retrieve the previously stored, provenance-tagged maintainer instruction | `planned-4192-s01-3547e2` |

### Attention: the fact was available but went unused

In the lost run `planned-4192-s01-bbb35b`, the comment-drafting request contained the exact maintainer fact. Its position was 52.5% of the request text, 14,762 characters from the nearest edge. The model nevertheless posted `label: bug` and `file: none`.

In the fixed run `planned-4192-s01-4c40ce`, the same fact appeared at 95.2% of the request, only 58 characters from the nearest edge. The model posted `label: feature` and `file: src/components/ExportButton.tsx`.

The fact was not missing from either request. This was an attention failure: information was available but did not influence the lost run's final decision. Making the relevant fact prominent corrected the observed behavior without requiring additional memory storage.

### Compaction: the fact disappeared from the drafting context

In the lost run `planned-4192-s01-696322`, model request 2 contained the maintainer fact. A `compaction` event appeared at trace line 14. The later comment-drafting request 5 no longer contained the fact, and the tracker comment stated `label: question` and `file: none`.

The compaction retained structured execution progress but discarded the earlier maintainer statement.

In the fixed run `planned-4192-s01-bcbfda`, request 3 contained the fact before compaction. The compaction event appeared at trace line 16, but a structured note outside the summarized history preserved the instruction. The comment-drafting request 6 still contained the fact, and the posted comment used `label: feature` and `file: src/components/ExportButton.tsx`.

This was a compaction-boundary failure. The appropriate fix was to preserve essential information outside the compacted conversation, not simply to expand the context window.

### Session: the fact did not carry into a new run

The stated run was `planned-4192-s01-fa2124`. Its model requests 2, 4, 5, and 6 contained the exact simulated maintainer instruction.

After successful triage, RepoMind persisted that instruction as Memory ID 13, with the following provenance:

- Kind: `procedural`
- Source: `user`
- Origin: `ingested`
- Confirmed: `true`
- Stated by: `simulated-ledgerline-maintainer`
- Scope: Ledgerline, issue 4192

In the lost run `planned-4192-s01-832947`, I used `STORE_MODE=empty`. Its `store_read` event returned `[]`, no model request contained the fact, and the final comment used `label: bug`.

In the fixed run `planned-4192-s01-3547e2`, I restored normal memory retrieval. The `store_read` event at trace line 5 returned record IDs `[11, 13, 14]`, including the active procedural rule in Memory ID 13. This retrieval occurred before repository search, as confirmed by the assignment checker.

The fixed run's comment-drafting request 4 contained the exact maintainer fact. The final comment used `label: feature` and `file: src/components/ExportButton.tsx`.

This was a session-boundary failure: the information had not been carried from the earlier run. The appropriate fix was persistent memory with provenance, scoped retrieval, and explicit treatment of confirmed maintainer instructions.

### Diagnosis across all four boundaries

The three experiments demonstrate that similar symptoms can have different causes:

- **Attention:** The fact is in the request but ignored. Make it more prominent or shorten distracting context.
- **Compaction:** The fact is removed from later requests. Preserve it in a structured note outside the summarized conversation.
- **Session:** The fact is unavailable in the next run. Retrieve it from a persistent memory store.
- **Restart:** An interrupted process loses execution state. Restore a durable checkpoint and replay safely using stable idempotency keys, as demonstrated in Part B.

A persistent memory store is therefore not the universal solution to forgetting. The correct intervention depends on identifying the boundary where the information was lost.

## AI assistance

I used ChatGPT to assist with implementation guidance, debugging, experiment design, verification scripts, and report writing for Assignment 4. The assistance included code suggestions for the planned executor, checkpoint recovery and persistent-memory workflows, and the attention, compaction, and session experiments. ChatGPT also helped draft and revise the explanations in `reports/LAB_04.md`, `CHANGES.md`, and `ARCHITECTURE.md`.

I applied and reviewed the changes locally. I verified Python syntax with `py_compile`, ran the unit test suite (`make test`: 88 tests, 2 skipped), inspected model-request records, execution traces, persistent-memory records, and tracker comments, and ran `make check-04`, which reported 82/82 automated points with no integrity flags.

The model used for the agent experiments was Google's Gemini `gemini-3.5-flash-lite` through the OpenAI-compatible API.
