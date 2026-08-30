"""``python -m redteam.run`` — the whole evaluation, one command.

    python -m redteam.run --all              run every baseline, print the report
    python -m redteam.run --freeze           (re)freeze the corpus + its hash
    python -m redteam.run --verify           check the corpus against its hash
    python -m redteam.run --all --out FILE   also write the markdown report
    python -m redteam.run --all --json FILE  also write machine-readable results

By default the run refuses to proceed if the live corpus hash does not match
the frozen one, because a number reported against an unfrozen corpus is exactly
the number the freeze exists to distrust. ``--allow-unfrozen`` overrides that
for local iteration, and says so loudly in the output.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from redteam import corpus
from redteam.harness import Baseline
from redteam.metrics import build_report, render


ALL_BASELINES = [Baseline.B0, Baseline.B1, Baseline.B2, Baseline.B3]


def run(baselines=ALL_BASELINES, progress=True):
    sessions = corpus.load_sessions()
    results: dict[Baseline, list] = {b: [] for b in baselines}

    for b in baselines:
        started = time.perf_counter()
        for session in sessions:
            outcome = session.run(b)
            results[b].append((session, outcome))
        if progress:
            elapsed = time.perf_counter() - started
            print(
                f"  {b.value} {b.label:<14} {len(sessions)} sessions "
                f"in {elapsed:.1f}s",
                file=sys.stderr,
            )
    return sessions, results


def to_json(report) -> dict:
    out = {
        "corpus_hash": report.corpus_hash,
        "corpus_verified": report.corpus_verified,
        "n_sessions": report.n_sessions,
        "n_attacks": report.n_attacks,
        "n_legit": report.n_legit,
        "baselines": {},
    }
    for b, r in report.baselines.items():
        out["baselines"][b.value] = {
            "recall": r.recall,
            "precision": r.precision,
            "f1": r.f1,
            "fpr": r.fpr,
            "tp": r.tp,
            "fp": r.fp,
            "fn": r.fn,
            "tn": r.tn,
            "denied_paise": r.denied_paise,
            "stepped_up_paise": r.stepped_up_paise,
            "held_paise": r.held_paise,
            "clean_allow": r.clean_allow,
            "per_class": {
                c: {"total": cr.total, "stopped": cr.stopped, "rate": cr.rate}
                for c, cr in r.per_class.items()
            },
            "latency_p50": r.pct(50),
            "latency_p95": r.pct(95),
            "latency_p99": r.pct(99),
        }
    return out


def main(argv=None) -> int:
    # The report uses ✓/✗/~ and ₹; Windows consoles default to cp1252 and
    # would raise on them. Reconfigure to UTF-8 where the stream supports it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="redteam.run", description=__doc__)
    parser.add_argument("--all", action="store_true", help="run every baseline")
    parser.add_argument("--freeze", action="store_true", help="(re)freeze the corpus")
    parser.add_argument("--verify", action="store_true", help="verify the corpus hash")
    parser.add_argument("--out", metavar="FILE", help="write the markdown report here")
    parser.add_argument("--json", metavar="FILE", help="write machine-readable results")
    parser.add_argument(
        "--allow-unfrozen",
        action="store_true",
        help="run even if the corpus does not match its frozen hash",
    )
    args = parser.parse_args(argv)

    if args.freeze:
        digest = corpus.freeze()
        print(f"Corpus frozen. SHA-256 = {digest}")
        return 0

    matches, live, frozen = corpus.verify()
    if args.verify:
        print(f"live   {live}")
        print(f"frozen {frozen}")
        print("MATCH" if matches else "MISMATCH")
        return 0 if matches else 1

    if frozen is None:
        print(
            "No frozen corpus found. Run `python -m redteam.run --freeze` first.",
            file=sys.stderr,
        )
        return 2
    if not matches and not args.allow_unfrozen:
        print(
            "Corpus hash does not match the frozen hash. The scenario "
            "definitions changed after the freeze.\n"
            f"  live   {live}\n  frozen {frozen}\n"
            "Re-freeze deliberately (`--freeze`) or pass `--allow-unfrozen`.",
            file=sys.stderr,
        )
        return 1

    print("Running baselines...", file=sys.stderr)
    _sessions, results = run(ALL_BASELINES)
    report = build_report(results, corpus_hash=live, corpus_verified=matches)

    markdown = render(report)
    print(markdown)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(markdown + "\n")
        print(f"\n[written] {args.out}", file=sys.stderr)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(to_json(report), fh, indent=2)
        print(f"[written] {args.json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
