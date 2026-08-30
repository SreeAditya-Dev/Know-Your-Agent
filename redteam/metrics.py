"""Turning session outcomes into the report the panel reads.

The headline is the comparison table: eleven attack classes down the side, four
defence postures across the top, and the exact point in the row where a class
stops being caught. B1 is the shipped state of the art — what a merchant
integrating Visa Trusted Agent Protocol or Cloudflare Web Bot Auth gets today —
so the columns to the right of B1 are, precisely, what identity-only defence
misses.

Everything else here exists to keep that headline honest: precision and recall
so the table is not hiding a wall of false positives, a false-positive *cost*
decomposed into denied vs stepped-up vs held rupees so "false positive" is a
number in money rather than a vibe, data-plane latency so the money path is
shown to never wait on anything, and an exception list of what the full gateway
still does not catch, because a clean sheet nobody believes scores worse than
an honest gap.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from kya.enums import Decision
from redteam.harness import Baseline, Outcome, Session
from redteam.scenarios import CLASS_NAMES, CLASS_ORDER


def rupees(paise: int) -> str:
    return f"₹{paise / 100:,.0f}"


@dataclass(slots=True)
class ClassResult:
    attack_class: str
    total: int
    stopped: int

    @property
    def rate(self) -> float:
        return self.stopped / self.total if self.total else 0.0

    @property
    def symbol(self) -> str:
        if self.total == 0:
            return "–"
        if self.stopped == self.total:
            return "✓"  # ✓
        if self.stopped == 0:
            return "✗"  # ✗
        return "~"


@dataclass(slots=True)
class BaselineReport:
    baseline: Baseline
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    #: Legitimate-traffic friction, in paise, by outcome.
    denied_paise: int = 0
    stepped_up_paise: int = 0
    held_paise: int = 0
    clean_allow: int = 0
    legit_total: int = 0
    per_class: dict[str, ClassResult] = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 1.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def fpr(self) -> float:
        """Fraction of legitimate sessions actively blocked (DENY/QUARANTINE)."""
        return self.fp / self.legit_total if self.legit_total else 0.0

    def pct(self, latency_p: float) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        k = min(len(ordered) - 1, int(round((latency_p / 100) * (len(ordered) - 1))))
        return ordered[k]


@dataclass(slots=True)
class Report:
    baselines: dict[Baseline, BaselineReport]
    #: (session, outcome) pairs from B3 that are honest failures worth listing.
    attack_exceptions: list[tuple[Session, Outcome]]
    false_positives: list[tuple[Session, Outcome]]
    n_sessions: int
    n_attacks: int
    n_legit: int
    corpus_hash: str
    corpus_verified: bool


def build_report(
    results: dict[Baseline, list[tuple[Session, Outcome]]],
    corpus_hash: str,
    corpus_verified: bool,
) -> Report:
    baselines: dict[Baseline, BaselineReport] = {}

    for baseline, pairs in results.items():
        rep = BaselineReport(baseline=baseline)
        per_class_total: dict[str, int] = {}
        per_class_stopped: dict[str, int] = {}

        for session, outcome in pairs:
            if session.is_attack:
                cls = session.attack_class or "?"
                per_class_total[cls] = per_class_total.get(cls, 0) + 1
                if outcome.stopped:
                    per_class_stopped[cls] = per_class_stopped.get(cls, 0) + 1
                    rep.tp += 1
                else:
                    rep.fn += 1
            else:
                rep.legit_total += 1
                if outcome.stopped:
                    rep.fp += 1
                else:
                    rep.tn += 1
                if outcome.decision is Decision.DENY:
                    rep.denied_paise += outcome.amount
                elif outcome.decision is Decision.QUARANTINE:
                    rep.held_paise += outcome.amount
                elif outcome.decision is Decision.STEP_UP:
                    rep.stepped_up_paise += outcome.amount
                else:
                    rep.clean_allow += 1
                # Data-plane latency is only meaningful where a full decision
                # was actually rendered on the money path.
                if baseline is Baseline.B3 and outcome.latencies:
                    rep.latencies.extend(outcome.latencies)

        for cls in CLASS_ORDER:
            if cls in per_class_total:
                rep.per_class[cls] = ClassResult(
                    cls, per_class_total[cls], per_class_stopped.get(cls, 0)
                )
        baselines[baseline] = rep

    # Honest failure lists, taken from the full gateway.
    b3 = results.get(Baseline.B3, [])
    attack_exceptions = [
        (s, o) for s, o in b3 if s.is_attack and not o.stopped
    ]
    false_positives = [
        (s, o) for s, o in b3 if not s.is_attack and o.decision is Decision.DENY
    ]

    n_attacks = sum(1 for s, _ in b3 if s.is_attack)
    n_legit = sum(1 for s, _ in b3 if not s.is_attack)

    return Report(
        baselines=baselines,
        attack_exceptions=attack_exceptions,
        false_positives=false_positives,
        n_sessions=len(b3),
        n_attacks=n_attacks,
        n_legit=n_legit,
        corpus_hash=corpus_hash,
        corpus_verified=corpus_verified,
    )


# --- rendering ---------------------------------------------------------------


def render(report: Report) -> str:
    order = [Baseline.B0, Baseline.B1, Baseline.B2, Baseline.B3]
    present = [b for b in order if b in report.baselines]
    lines: list[str] = []

    lines.append("# KYA red-team evaluation")
    lines.append("")
    verified = "verified ✓" if report.corpus_verified else "MISMATCH ✗"
    lines.append(
        f"Corpus: **{report.n_sessions}** sessions "
        f"({report.n_attacks} attacks across {len(CLASS_NAMES)} classes, "
        f"{report.n_legit} legitimate). "
        f"Frozen hash `{report.corpus_hash[:16]}…` ({verified})."
    )
    lines.append("")
    lines.append(
        "> B1 is the shipped state of the art (Visa Trusted Agent Protocol / "
        "Cloudflare Web Bot Auth). Everything B1 misses and B3 catches is the "
        "thesis of this project, stated in numbers."
    )
    lines.append("")

    # --- comparison table ---------------------------------------------------
    lines.append("## What each defence posture catches")
    lines.append("")
    header = "| Attack class | " + " | ".join(
        f"{b.value} {b.label}" for b in present
    ) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(present) + 1))
    for cls in CLASS_ORDER:
        row = [f"{cls} {CLASS_NAMES[cls]}"]
        for b in present:
            cr = report.baselines[b].per_class.get(cls)
            row.append(cr.symbol if cr else "–")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("✓ all stopped · ~ some stopped · ✗ none stopped")
    lines.append("")

    # --- precision / recall -------------------------------------------------
    lines.append("## Detection quality")
    lines.append("")
    lines.append(
        "| Posture | Attacks stopped | Recall | Precision | F1 | "
        "False-positive rate |"
    )
    lines.append("|---|---|---|---|---|---|")
    for b in present:
        r = report.baselines[b]
        attacks = r.tp + r.fn
        lines.append(
            f"| {b.value} {b.label} | {r.tp}/{attacks} | {r.recall:.0%} | "
            f"{r.precision:.0%} | {r.f1:.2f} | {r.fpr:.1%} |"
        )
    lines.append("")

    # --- false-positive cost ------------------------------------------------
    lines.append("## False-positive cost on legitimate traffic")
    lines.append("")
    lines.append(
        "A false positive is not a lost sale by default. The tier ladder turns "
        "most of them into a *bounded* or *stepped-up* sale, so the cost is "
        "decomposed by what actually happened to the money — only the "
        "denied column is revenue genuinely refused."
    )
    lines.append("")
    lines.append(
        "| Posture | Clean allow | Denied (lost) | Stepped up (friction) | "
        "Held for review |"
    )
    lines.append("|---|---|---|---|---|")
    for b in present:
        r = report.baselines[b]
        lines.append(
            f"| {b.value} {b.label} | {r.clean_allow}/{r.legit_total} | "
            f"{rupees(r.denied_paise)} | {rupees(r.stepped_up_paise)} | "
            f"{rupees(r.held_paise)} |"
        )
    lines.append("")

    # --- latency ------------------------------------------------------------
    b3 = report.baselines.get(Baseline.B3)
    if b3 and b3.latencies:
        lines.append("## Data-plane latency (B3, inline path, no model)")
        lines.append("")
        lines.append("| p50 | p95 | p99 | samples | budget |")
        lines.append("|---|---|---|---|---|")
        lines.append(
            f"| {b3.pct(50):.2f} ms | {b3.pct(95):.2f} ms | {b3.pct(99):.2f} ms | "
            f"{len(b3.latencies)} | 50 ms |"
        )
        lines.append("")
        lines.append(
            "The money decision is fully deterministic; no gate can reach a "
            "model or a network call, which is why the obligation-mismatch "
            "class is cleared off the money path rather than inline."
        )
        lines.append("")

    # --- exception list -----------------------------------------------------
    lines.append("## Exception list — what B3 (full KYA) does not catch")
    lines.append("")
    if not report.attack_exceptions:
        lines.append(
            "No attack in the corpus reached ALLOW under the full gateway. "
            "This is stated as a fact about *this corpus*, not a claim of "
            "completeness — the classes here are the ones we authored, and "
            "the honest limitation is coverage, not evasion. See "
            "`docs/07-limitations.md`."
        )
    else:
        lines.append("| Session | Class | Decision | Note |")
        lines.append("|---|---|---|---|")
        for s, o in report.attack_exceptions[:40]:
            note = o.note or (o.clearing or "")
            lines.append(
                f"| {s.session_id} | {s.attack_class} {CLASS_NAMES.get(s.attack_class, '')} "
                f"| {o.decision.value} | {note} |"
            )
    lines.append("")

    if report.false_positives:
        lines.append("### Legitimate sessions denied under B3 (true lost sales)")
        lines.append("")
        lines.append("| Session | Amount | Reason codes |")
        lines.append("|---|---|---|")
        for s, o in report.false_positives[:40]:
            lines.append(
                f"| {s.session_id} | {rupees(o.amount)} | "
                f"{', '.join(o.reason_codes) or '—'} |"
            )
        lines.append("")

    return "\n".join(lines)
