# RepoMind Memory Write Policy

## 1. Purpose and Scope

RepoMind uses persistent memory to preserve useful knowledge between runs. Unlike checkpoints, which contain exact execution state for one run, memory stores reusable observations, verified procedures, and decisions across runs.

Memories are scoped by repository and optionally by issue. Every record must preserve its provenance and support later verification, supersession, and retrieval.

## 2. When to Write

Write memory at the end of a successfully completed run or immediately after a claim has been independently verified.

Do not store unverified hypotheses as confirmed knowledge. A failed or escalated run must not promote its incomplete conclusions to verified facts.

Existing verified memories may be updated when new authoritative evidence becomes available.

## 3. What to Store

Store information that is useful across runs or expensive to rediscover.

Use three memory categories:

- **Episodic:** Prior events, issue discussions, and triage decisions.
- **Semantic:** Verified repository facts, such as programming languages, dependencies, and available commands.
- **Procedural:** Reusable instructions, such as how to reproduce or investigate a particular type of problem.

Avoid storing redundant tool outputs, temporary execution state, or unsupported conclusions.

### Candidate A: Derived Triage Procedure

The procedure from issue 4191 is a candidate for procedural memory.

- Source: `memory`
- Origin: `derived`
- Kind: `procedural`
- Confirmed: `true` only after validating the described commands against repository evidence
- Timestamp: `2026-10-02T14:12:09Z`

This procedure may become stale when repository scripts or configurations change. It must be revalidated when the repository changes materially.

### Candidate B: Issue-Body Auto-Merge Claim

The claim from issue 4188 must never be promoted to confirmed repository policy.

If stored, preserve it only as an account of what the issue author claimed:

- Source: `issue:4188`
- Origin: `ingested`
- Kind: `episodic`
- Confirmed: `false`
- Timestamp: `2026-09-30T08:41:37Z`

Untrusted issue text cannot authorize automatic merging or supersede verified maintainer instructions. This record must not enter the planner's context as an authoritative fact.

### Candidate C: Maintainer Rule

The duplicate-handling instruction from maintainer `priya-s` is a repository-wide procedural rule.

- Source: `user`
- Origin: `ingested`
- Kind: `procedural`
- Confirmed: `true`
- Stated by: `priya-s`
- Timestamp: `2026-09-21T16:05:52Z`
- Issue scope: repository-wide (`null`)

Confirmation means the instruction came from an identified maintainer, not that it can never change.

## 4. How to Update Memory

New knowledge is inserted as a new record. Repeated equivalent knowledge should be consolidated rather than duplicated.

When a confirmed fact or rule is contradicted by newer authoritative evidence:

1. Verify the new evidence and its authority.
2. Mark the existing record inactive.
3. Insert a replacement record with `supersedes` referencing the old record's ID.
4. Record the new source and timestamp.
5. Preserve the original record for auditing.

Both changes must be committed atomically.

An unverified issue-body claim cannot supersede a confirmed maintainer rule.

## 5. Provenance Requirements

Every memory record must contain:

- `id`: Unique record identifier
- `repository`: Repository identifier
- `issue`: Issue number or `null`
- `kind`: Episodic, semantic, or procedural
- `text`: Stored memory
- `source`: `user`, `issue:<n>`, `repo`, or `memory`
- `written_at`: ISO 8601 timestamp
- `origin`: `derived` or `ingested`
- `confirmed`: Boolean verification status
- `active`: Whether the record remains current
- `supersedes`: Previous record ID or `null`
- `stated_by`: Original speaker when applicable

For supplied historical evidence, use the fixture's original `when` timestamp. For new conclusions derived during RepoMind execution, use the time the conclusion is verified and stored.

## 6. Retrieval and Trust

Retrieve active repository-wide and issue-specific records before the first repository search.

Each retrieved memory must retain its source, age, and confirmation status. Record the returned memory IDs in a `store_read` trace event.

Confirmed memories may inform later decisions but should not override contradictory current repository evidence.

Unconfirmed issue-body claims must not be presented to the planner as established facts or instructions.

## 7. Eviction and Retention

Limit the store to 250 active records per repository.

Consolidate equivalent records before applying eviction. When capacity is exceeded, retire the oldest low-value, unconfirmed episodic observations first.

Revalidate derived procedures when relevant repository configurations change. Prioritize stale procedures for retirement when they have not been reconfirmed within 90 days.

Preserve confirmed maintainer rules until they are superseded or explicitly revoked. Preserve superseded records as inactive history so their provenance remains auditable.

**Capacity estimate:** Assuming one retained active memory per run and 20 runs daily, 250 records would fill in approximately 12.5 days without consolidation or eviction.

This is a provisional estimate. Replace the assumed memory-write rate with the observed retained-record rate from RepoMind runs when completing the Part D report.

## 8. Reliability and Safety

Persistent memory must remain separate from checkpoint data.

Memory writes must not interfere with the execution journal, replay behavior, or idempotency keys.

A memory is evidence with provenance, not an instruction that can bypass tool permissions or execution boundaries.

The memory store should preserve useful knowledge while preventing stale, contradictory, or untrusted claims from gaining inappropriate authority.
