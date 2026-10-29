
"""Seed the memory store using the three Part D fixtures."""

import argparse
from pathlib import Path

import yaml

from state.store import Store

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "memory"


def build_record(candidate):
    """Convert a fixture into a provenance-aware memory record."""

    name = candidate["candidate"]
    issue = int(candidate["issue"])

    if name == "A":
        kind = "procedural"
        source = "memory"
        origin = "derived"
        confirmed = True
        stated_by = None

    elif name == "B":
        kind = "episodic"
        source = f"issue:{issue}"
        origin = "ingested"
        confirmed = False
        stated_by = None

    elif name == "C":
        kind = "procedural"
        source = "user"
        origin = "ingested"
        confirmed = True
        stated_by = candidate["who"].split(",")[0].strip()
        issue = None  # Repository-wide maintainer rule

    else:
        raise ValueError(f"Unknown candidate: {name}")

    return {
        "repository": candidate["repository"],
        "issue": issue,
        "kind": kind,
        "text": candidate["seen"].strip(),
        "source": source,
        "written_at": candidate["when"],
        "origin": origin,
        "confirmed": confirmed,
        "active": True,
        "supersedes": None,
        "stated_by": stated_by,
    }


def seed(store):
    """Insert the candidates without creating duplicate records."""

    for path in sorted(FIXTURES.glob("*.yaml")):
        candidate = yaml.safe_load(path.read_text())
        record = build_record(candidate)
        name = candidate["candidate"]

        # Check all historical records, including inactive ones.
        # This avoids restoring a previously superseded rule.
        existing = next(
            (
                row for row in store.records()
                if row["repository"] == record["repository"]
                and row["issue"] == record["issue"]
                and row["source"] == record["source"]
                and row["text"] == record["text"]
            ),
            None,
        )

        if existing is not None:
            print(f"Candidate {name}: already stored, ID={existing['id']}")
            continue

        memory_id = store.write(record)

        print(
            f"Candidate {name}: stored ID={memory_id}, "
            f"kind={record['kind']}, "
            f"source={record['source']}, "
            f"confirmed={record['confirmed']}"
        )

    print(f"Total records: {len(store.records())}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Optional database path for testing",
    )
    args = parser.parse_args()

    seed(Store(args.db))
