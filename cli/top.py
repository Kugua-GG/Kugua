"""
kugua top — live terminal dashboard for kugua kernel subsystems.

Polls kernel state every 2s and renders a four-panel view:
  TrustLevel  — trust gradient + audit trail
  CSD         — critical slowing detector tracking pairs
  Negentropy  — 5-dimension health scores
  Knowledge   — KB level distribution + graph stats

Usage:
  kugua-top              # default 2s refresh
  kugua-top --interval 5 # 5s refresh
  kugua-top --once       # print once and exit
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

# ── Windows GBK ──────────────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── ANSI helpers ─────────────────────────────────────────────

def _sgr(*codes: int) -> str:
    return f"\033[{';'.join(map(str, codes))}m"

R = _sgr(0)        # reset
B = _sgr(1)        # bold
D = _sgr(2)        # dim
RD = _sgr(31)      # red
GR = _sgr(32)      # green
YL = _sgr(33)      # yellow
CY = _sgr(36)      # cyan
MG = _sgr(35)      # magenta
BG_RD = _sgr(41)   # bg red
BG_GR = _sgr(42)   # bg green

W = 72  # panel width


def _bar(val: float, w: int = 16, hi: float = 100.0) -> str:
    """Render a mini progress bar."""
    p = max(0, min(1.0, val / hi))
    n = int(p * w)
    bar = "█" * n + "░" * (w - n)
    if val >= 80:
        color = GR
    elif val >= 60:
        color = YL
    elif val >= 40:
        color = YL
    else:
        color = RD
    return f"{color}{bar}{R}"


def _header(text: str) -> str:
    return f"{B}{CY}  {text}{' ' * (W - 3 - _vlen(text))}{R}"


def _vlen(s: str) -> int:
    """Visible length (strip ANSI escapes)."""
    import re
    return len(re.sub(r'\033\[[0-9;]*m', '', str(s)))


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _vlen(s))


def _clear():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _hline(label: str = "") -> str:
    if label:
        return f"{D}── {label} {'─' * max(0, W - 5 - len(label))}{R}"
    return f"{D}{'─' * W}{R}"


# ═══════════════════════════════════════════════════════════════
# Data gathering
# ═══════════════════════════════════════════════════════════════

def _gather() -> Dict[str, Any]:
    """Collect all subsystem state. Returns dict ready for rendering."""
    from kugua.config import KuguaConfig
    from kugua.safety import SafetyManager
    from kugua.states import StatesMachine
    from kugua.negentropy import Negentropy, NegentropyHistory
    from kugua.critical_slowing import CriticalSlowingDetector
    from kugua.efficacy import DoubleLoopEfficacyTracker

    cfg = KuguaConfig.from_env()
    arts = cfg.artifacts_dir

    safety = SafetyManager(cfg)
    sm = StatesMachine(cfg)
    ne = Negentropy(sm.load_state(), efficacy=None)
    ne_hist = NegentropyHistory(artifacts_dir=arts)
    csd = CriticalSlowingDetector(artifacts_dir=arts)
    efficacy = DoubleLoopEfficacyTracker(artifacts_dir=arts)

    # KB
    try:
        from kugua.knowledge import KnowledgeBase
        kb = KnowledgeBase(cfg)
        kb_stats = kb.effective_stats()
    except Exception:
        kb_stats = {}

    return {
        "trust": safety._trust_level.value,
        "emergency": safety._state.get("emergency_stop", False),
        "audit": safety.get_audit_summary(),
        "negentropy": ne.to_dict(),
        "ne_history": ne_hist.to_dict(),
        "csd": csd.to_dict(),
        "efficacy": efficacy.to_dict(),
        "kb": kb_stats,
    }


# ═══════════════════════════════════════════════════════════════
# Panel renderers
# ═══════════════════════════════════════════════════════════════

def _panel_trust(data: Dict[str, Any]) -> List[str]:
    trust = data["trust"]
    audit = data["audit"]
    emergency = data["emergency"]

    lvl_bar = _bar(trust * 20, w=20, hi=5.0)
    em = f" {BG_RD} EMERGENCY STOP {R}" if emergency else ""

    lines = [
        _header(f"TrustLevel  L{trust}/5{em}"),
        _hline(),
        f"  Level    {lvl_bar}  L{trust}",
        f"  Checks   {audit['total_checks']:>6} total  |  {audit['total_denials']} denied  |  {audit['denial_rate']:.1%} rate",
        f"",
        f"  {B}Recent denials:{R}",
    ]

    recent = audit.get("recent_denials", [])
    if recent:
        for e in recent[:3]:
            ts = e["timestamp"][:19]
            lines.append(f"  {D}{ts}{R}  {RD}BLOCKED{R}  {e['operation']}")
            lines.append(f"           {D}{e['reason'][:60]}{R}")
    else:
        lines.append(f"  {D}(no denials — clean run){R}")

    return lines


def _panel_csd(data: Dict[str, Any]) -> List[str]:
    csd = data["csd"]
    tracked = csd.get("tracked_pairs", 0)
    critical = csd.get("critical_count", 0)
    signals = csd.get("signals", [])

    lines = [
        _header(f"CriticalSlowingDetector"),
        _hline(),
        f"  Pairs tracked  {tracked:>4}   |   Critical  {RD if critical else GR}{critical}{R}",
        f"",
    ]

    if signals:
        lines.append(f"  {B}Signals:{R}")
        for s in signals[:8]:
            icon = "R" if s.get("critical") else ("Y" if s.get("significant") else ".")
            color = RD if s.get("critical") else (YL if s.get("significant") else D)
            tau = s.get("kendall_tau", 0)
            p = s.get("p_value", 1)
            comp = s.get("composite_index", 0)
            lines.append(
                f"  {color}{icon}{R} {s['error_type']}:{s['gv_id']}  "
                f"n={s['sample_count']}  tau={tau:+.3f}  p={p:.4f}  csd={comp:.2f}"
            )
    else:
        lines.append(f"  {D}No tracked pairs. Run `kugua-demo --scenario=collapse` to seed data.{R}")

    return lines


def _panel_negentropy(data: Dict[str, Any]) -> List[str]:
    ne = data["negentropy"]
    hist = data["ne_history"]

    comp = ne.get("composite", 0)
    tier = "excellent" if comp >= 80 else ("good" if comp >= 60 else ("needs-work" if comp >= 40 else "critical"))

    dims = [
        ("Process Order  ", ne.get("process_order", 0)),
        ("Intent Anchor  ", ne.get("intent_anchoring", 0)),
        ("Knowledge Eff  ", ne.get("knowledge_efficacy", 0)),
        ("Info Fidelity  ", ne.get("information_fidelity", 0)),
        ("DoubleLoop Eff ", ne.get("double_loop_efficacy", 0)),
    ]

    lines = [
        _header(f"Negentropy  composite={comp:.0f}% ({tier})"),
        _hline(),
    ]

    for name, score in dims:
        lines.append(f"  {name}  {_bar(score, w=24)}  {score:5.1f}%")

    lines.append("")
    if hist.get("count", 0) > 0:
        delta = hist.get("delta", 0)
        trend = hist.get("trend", 0)
        degrading = hist.get("degrading", False)
        d_sign = "+" if delta >= 0 else ""
        t_sign = "+" if trend >= 0 else ""
        deg = f" {RD}DEGRADING{R}" if degrading else ""
        lines.append(f"  History  {hist['count']} snapshots  |  delta={d_sign}{delta:.1f}  trend={t_sign}{trend:.2f}{deg}")
    else:
        lines.append(f"  History  {D}no snapshots yet (run 5+ tasks to build history){R}")

    return lines


def _panel_knowledge(data: Dict[str, Any]) -> List[str]:
    kb = data.get("kb", {})
    eff = data.get("efficacy", {})

    levels = kb.get("level_distribution", {})
    l3 = levels.get("L3", 0)
    l2 = levels.get("L2", 0)
    l1 = levels.get("L1", 0)
    total = l3 + l2 + l1

    lines = [
        _header(f"KnowledgeBase  {total} entries"),
        _hline(),
        f"  L3 (verified 10+)  {_bar(l3 * 100 / max(total, 1), w=16, hi=100.0)}  {l3:>4}",
        f"  L2 (verified 3+)   {_bar(l2 * 100 / max(total, 1), w=16, hi=100.0)}  {l2:>4}",
        f"  L1 (single)        {_bar(l1 * 100 / max(total, 1), w=16, hi=100.0)}  {l1:>4}",
        f"",
    ]

    # Efficacy
    verified = eff.get("verified_events", 0)
    reverted = eff.get("reverted_events", 0)
    ent_reduction = eff.get("total_entropy_reduction", 0)
    pending = eff.get("pending_events", 0)

    lines.append(f"  DoubleLoop: {verified} verified  |  {reverted} reverted  |  {pending} pending")
    lines.append(f"  Entropy reduction: {ent_reduction:.1f} bits")

    # Graph
    graph_nodes = kb.get("graph_nodes", 0)
    if graph_nodes:
        lines.append(f"  GraphKB: {graph_nodes} nodes")

    return lines


# ═══════════════════════════════════════════════════════════════
# Render & main loop
# ═══════════════════════════════════════════════════════════════

def _render(data: Dict[str, Any]) -> str:
    panels = [
        _panel_trust(data),
        _panel_csd(data),
        _panel_negentropy(data),
        _panel_knowledge(data),
    ]

    # Compute max height per panel for alignment
    out: List[str] = []
    out.append(f"{B}  kugua top v0.3  —  press Ctrl+C to exit{R}")
    out.append("")

    for p in panels:
        for line in p:
            out.append(_pad(line, W))
        out.append("")

    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description="kugua top — live kernel subsystem monitor")
    p.add_argument("--interval", "-i", type=float, default=2.0, help="Refresh interval in seconds (default: 2)")
    p.add_argument("--once", "-1", action="store_true", help="Print once and exit")
    args = p.parse_args()

    print(f"{B}kugua top v0.3{R}  loading subsystems...", end="", flush=True)

    try:
        # First gather to verify everything loads
        data = _gather()
    except Exception as e:
        print(f"\n{RD}Error initializing kernel: {e}{R}")
        print(f"{D}Ensure kugua is installed: pip install -e .{R}")
        return 1

    if args.once:
        _clear()
        print(_render(data))
        return 0

    try:
        while True:
            _clear()
            data = _gather()
            print(_render(data))
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        _clear()
        print(f"{GR}kugua top stopped.{R}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
