"""
kugua demo — 内置场景演示 SafetyManager 和 CriticalSlowingDetector 的运行时行为。

场景：
  high_risk_rm — Agent 执行操作序列，高危操作被 Guardian 拦截
  collapse     — Agent 陷入恶化循环，CSD 检测临界慢化并触发 KillSwitch

用法：
  kugua-demo --scenario=high_risk_rm
  kugua-demo --scenario=collapse
"""

import argparse
import sys
import time
from pathlib import Path
from typing import List, Tuple

# ── Windows GBK 编码兼容 ────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── ANSI 颜色（跨平台，Windows Terminal 原生支持）───────────
C = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "red":     "\033[31m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "blue":    "\033[34m",
    "magenta": "\033[35m",
    "cyan":    "\033[36m",
    "white":   "\033[37m",
    "bg_red":  "\033[41m",
    "bg_green":"\033[42m",
}


def _c(color: str, text: str) -> str:
    """Wrap text in ANSI color codes."""
    return f"{C.get(color, '')}{text}{C['reset']}"


def _box(title: str, lines: List[str], color: str = "cyan") -> str:
    """Draw a bordered box with title."""
    width = 64
    top = f"┌─ {title} {'─' * max(0, width - len(title) - 4)}┐"
    mid = "\n".join(f"│ {line:<{width-2}} │" for line in lines)
    bot = f"└{'─' * width}┘"
    return _c(color, f"{top}\n{mid}\n{bot}")


def _banner(text: str) -> str:
    """Full-width banner."""
    return _c("bold", f"\n{'═' * 66}\n  {text}\n{'═' * 66}\n")


# ═══════════════════════════════════════════════════════════════
# Scenario 1: high_risk_rm
# ═══════════════════════════════════════════════════════════════

def scenario_high_risk_rm():
    """演示 SafetyManager 拦截高危操作。"""
    from kugua.safety import SafetyManager, TrustLevel, OPERATION_PERMISSIONS

    safety = SafetyManager()
    # 从 L3 起步——模拟一个"可读写文件"的 Agent，但不能执行 shell
    safety._trust_level = TrustLevel(3)

    print(_banner("Scenario 1: Guardian 高危操作拦截"))
    print(_c("dim", "模拟场景：Agent 被允许读写文件，但试图 rm -rf / 和 sudo\n"))
    print(_c("dim", f"初始信任等级: L{safety._trust_level.value}  |  Error Budget: 10/10\n"))

    # 定义操作序列
    actions: List[Tuple[str, str]] = [
        ("read_file",    "读取 ./config.yaml"),
        ("write_file",   "写入 ./output.json"),
        ("execute_cmd",  "执行 pip install numpy"),
        ("rm_rf",        "rm -rf / --no-preserve-root"),
        ("sudo",         "sudo systemctl stop firewall"),
    ]

    denied_count = 0
    for i, (action, detail) in enumerate(actions, 1):
        print(_c("bold", f"── Action #{i} ──────────────────────────────────────────────"))
        print(f"  {_c('yellow', '⏳')} Agent 请求: {_c('white', action)}")
        print(f"     {_c('dim', detail)}")

        time.sleep(0.3)  # 体感节奏

        allowed, reason = safety.check_permission(action)

        if allowed:
            print(f"  {_c('green', '✅ ALLOWED')}   {_c('dim', reason)}")
        else:
            denied_count += 1
            print(f"  {_c('red', '🛡️  BLOCKED')}   {_c('red', reason)}")
            if action == "rm_rf":
                print(f"     {_c('bold', 'L5 永久禁止')} — 此操作在任何信任等级下均不可执行")

        # 显示当前预算
        remaining = 10 - denied_count
        budget_color = "green" if remaining > 5 else ("yellow" if remaining > 2 else "red")
        bar_filled = "█" * remaining
        bar_empty = "░" * denied_count
        print(f"  {_c('dim', f'Trust: L{safety._trust_level.value}  |  Budget: ')}{_c(budget_color, f'{bar_filled}{bar_empty} {remaining}/10')}")
        print()

    # ── 审计摘要 ──
    summary = safety.get_audit_summary()
    print(_box("📋 审计摘要", [
        f"总检查次数:   {summary['total_checks']}",
        f"拒绝次数:     {_c('red', str(summary['total_denials']))}",
        f"拒绝率:       {summary['denial_rate']:.0%}",
        "",
        "最近拒绝记录:",
    ] + [
        f"  [{_c('dim', e['timestamp'][:19])}] {_c('red', 'BLOCKED')} {e['operation']} — {e['reason']}"
        for e in summary['recent_denials']
    ], "magenta"))

    print(_c("green", "\nDemo complete. Guardian blocked all high-risk operations.\n"))
    print(_c("dim", "Integrate with your agent: call safety.check_permission(action) before each tool call."))
    print(_c("dim", "Scaffold a guarded agent template: kugua-init --framework=langgraph\n"))


# ═══════════════════════════════════════════════════════════════
# Scenario 2: collapse
# ═══════════════════════════════════════════════════════════════

def scenario_collapse():
    """演示 CSD 临界慢化检测 -> KillSwitch 拉闸。"""
    import tempfile
    import shutil
    from kugua.critical_slowing import CriticalSlowingDetector
    from kugua.safety import SafetyManager, TrustLevel

    tmpdir = Path(tempfile.mkdtemp(prefix="kugua_demo_"))
    csd = CriticalSlowingDetector(artifacts_dir=tmpdir, min_samples=5, p_threshold=0.05)
    safety = SafetyManager()
    safety._trust_level = TrustLevel(3)

    print(_banner("Scenario 2: 临界慢化 -> 死循环预警 -> KillSwitch"))
    print(_c("dim", "模拟场景：Agent 陷入死循环，同一个错误反复出现，恢复时间越来越长\n"))
    print(_c("dim", f"CSD 参数: min_samples=5  p_threshold=0.05  初始 Trust: L{safety._trust_level.value}\n"))

    error_type = "logical_error"
    gv_id = "loop_detection"

    # 18 轮渐进恶化——前 10 轮在噪音中缓慢上升，后 8 轮加速恶化
    # 目标：第 11 轮左右出现预警，第 13 轮左右触发临界
    recovery_sequence = [
        0.5, 0.8, 0.4, 0.7, 0.5, 0.9, 0.6, 0.7, 0.5, 0.8,  # 1-10: 平稳振荡
        1.2, 1.8, 2.5, 3.5, 5.0, 7.0, 10.0, 14.0,           # 11-18: 加速恶化
    ]

    print(_c("cyan", "┌─ 时间线 ──────────────────────────────────────────────────────┐"))

    kill_engaged = False
    warn_step = None
    kill_step = None

    for i, recovery_s in enumerate(recovery_sequence, 1):
        csd.record_failure(
            error_type=error_type,
            gv_ids=[gv_id],
            recovery_time_s=recovery_s,
            task_id=f"demo_loop_{i}",
        )

        signal = csd.detect(error_type, gv_id)
        n = signal.sample_count

        # 趋势箭头
        trend_arrow = {1: "UP", -1: "DOWN", 0: "-"}[signal.trend]
        if signal.critical:
            csd_color = "red"
        elif signal.significant:
            csd_color = "yellow"
        else:
            csd_color = "dim"

        # 简洁行格式
        print(
            f"│ {_c('bold', f'#{i:2d}')}  "
            f"rt={_c('yellow', f'{recovery_s:4.1f}s')}  "
            f"n={n:2d}  "
            f"tau={signal.kendall_tau:+.3f}  "
            f"p={_c(csd_color, f'{signal.p_value:.4f}')}  "
            f"csd={signal.composite_index:.2f}  "
            f"{trend_arrow}"
            f"{' ' * 20}"
            f"│"
        )

        # 检测状态变化：p<0.10=早期预警, signal.critical=拉闸
        if not kill_engaged and n >= 5:
            if signal.critical:
                if kill_step is None:
                    kill_step = i
                    print(
                        f"│ {_c('red', '>> CRITICAL SLOWING: p=' + f'{signal.p_value:.4f}' + ' tau=' + f'{signal.kendall_tau:+.3f}')}"
                        f"{' ' * 14}│"
                    )
                    time.sleep(0.3)
                    safety.kill_switch(
                        reason=f"CSD critical slowing — {error_type}:{gv_id} "
                               f"tau={signal.kendall_tau:.3f} p={signal.p_value:.4f}"
                    )
                    kill_engaged = True
                    print(
                        f"│ {_c('bg_red', ' KILLSWITCH ENGAGED ')}"
                        f"{' ' * 41}│"
                    )
                    if warn_step:
                        window = kill_step - warn_step
                        print(
                            f"│ {_c('red', f'Warning at #{warn_step} -> Kill at #{kill_step}  window={window} rounds')}"
                            f"{' ' * 12}│"
                        )
                    else:
                        print(
                            f"│ {_c('red', f'Escalation too fast: no warning window before critical threshold')}"
                            f"{' ' * 6}│"
                        )
            elif signal.p_value < 0.10 and warn_step is None:
                warn_step = i
                print(
                    f"│ {_c('yellow', '>> EARLY WARNING: recovery time trending up (p<0.10)')}"
                    f"{' ' * 17}│"
                )

        time.sleep(0.2)

    print(_c("cyan", "└──────────────────────────────────────────────────────────────┘"))
    print()

    # ── 终态摘要 ──
    if kill_engaged:
        window_str = f"{kill_step - warn_step} rounds" if warn_step else "none (escalation too fast)"
        print(_box("System Final State", [
            f"KillSwitch:  {_c('red', 'ENGAGED')}",
            f"Reason:      {safety._state.get('kill_reason', 'N/A')[:55]}",
            f"Warn round:  {warn_step if warn_step else _c('red', 'N/A')}",
            f"Kill round:  {kill_step}",
            f"Window:      {window_str}",
        ], "red"))
    else:
        print(_box("System Final State", [
            "KillSwitch: NOT engaged (threshold not reached)",
        ], "green"))

    # CSD 最终信号
    final = csd.detect(error_type, gv_id)
    print(_box("CSD Final Signal", [
        f"Pair:        {error_type}:{gv_id}",
        f"Samples:     {final.sample_count}",
        f"Kendall tau: {final.kendall_tau:+.4f}",
        f"p-value:     {final.p_value:.6f}",
        f"CSD compos:  {final.composite_index:.2f} (>=0.67 = high confidence)",
        f"EWS dims:    {final.ews_dimensions}/3 (tau + Fisher + AR1)",
        f"Summary:     {final.ews_summary}",
    ], "cyan"))

    print(_c("green", "\nDemo complete. CSD warned before collapse -> KillSwitch engaged.\n"))
    print(_c("dim", "CSD uses 3 EWS dimensions (tau + Fisher info + AR1) for cross-validation."))
    print(_c("dim", "Run `kugua-top` for live CSD monitoring.\n"))

    # Cleanup
    try:
        shutil.rmtree(tmpdir)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

SCENARIOS = {
    "high_risk_rm": scenario_high_risk_rm,
    "collapse": scenario_collapse,
}


def main():
    p = argparse.ArgumentParser(
        description="kugua demo — run built-in scenarios showcasing SafetyManager and CSD behavior",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kugua-demo --scenario=high_risk_rm   # Guardian blocks rm -rf /
  kugua-demo --scenario=collapse       # CSD detects critical slowing -> KillSwitch
        """,
    )
    p.add_argument(
        "--scenario", "-s",
        choices=list(SCENARIOS.keys()),
        default="high_risk_rm",
        help="Scenario to run (default: high_risk_rm)",
    )
    args = p.parse_args()

    print(_c("bold", f"\n  kugua demo v0.3"))
    print(_c("dim", "  No API key required. All scenarios run against the local kernel.\n"))

    try:
        SCENARIOS[args.scenario]()
    except ImportError as e:
        print(_c("red", f"\nImport error: {e}"))
        print(_c("dim", "  Ensure kugua is installed: pip install -e ."))
        return 1
    except Exception as e:
        print(_c("red", f"\nRuntime error: {e}"))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
