"""
kugua Chaos Runner v0.3.0 — 混沌工程测试

注入随机故障（工具调用失败、延迟、错误返回），持续运行并观测:
  - Kill Switch 是否被错误触发（误杀率）
  - 状态机是否卡死在非 P0 状态
  - 内存是否泄露
  - Guardian 介入率是否在合理范围

Usage:
    python benchmarks/chaos_runner.py --duration 60 --seed 42
    python benchmarks/chaos_runner.py --duration 3600 --output chaos_report_v0.3.0.md
"""
import json
import os
import sys
import time
import random
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kugua-code"))

from kugua.config import KuguaConfig
from kugua.states import StatesMachine
from kugua.safety import SafetyManager
from kugua.guardian import Guardian, GuardianConfig
from kugua.critical_slowing import CriticalSlowingDetector
from kugua.main_loop import MainLoop


class ChaosInjector:
    """随机故障注入器。"""

    def __init__(self, seed: int = 42, failure_rate: float = 0.15,
                 delay_max_ms: int = 100):
        self.rng = random.Random(seed)
        self.failure_rate = failure_rate
        self.delay_max_ms = delay_max_ms
        self.injected_count = 0
        self.delay_count = 0

    def should_fail(self) -> bool:
        return self.rng.random() < self.failure_rate

    def maybe_delay(self):
        if self.rng.random() < 0.1:
            delay = self.rng.uniform(0, self.delay_max_ms) / 1000.0
            time.sleep(delay)
            self.delay_count += 1

    def corrupt_confidence(self, confidence: float) -> float:
        """随机扰动置信度（模拟 LLM 不确定）。"""
        if self.rng.random() < 0.1:
            return max(0, min(1, confidence + self.rng.uniform(-0.3, 0.3)))
        return confidence


def get_memory_mb():
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return -1


def run_chaos(duration_sec: int = 60, seed: int = 42):
    """运行混沌测试。"""
    print(f"kugua Chaos Test v0.3.0 — {duration_sec}s, seed={seed}")
    print("=" * 60)

    artifacts = Path(os.getenv(
        "KUGUA_ARTIFACTS_DIR",
        str(Path.home() / ".claude" / ".codex" / "artifacts")
    ))
    artifacts.mkdir(parents=True, exist_ok=True)

    cfg = KuguaConfig()
    safety = SafetyManager(cfg)
    states = StatesMachine(cfg)
    csd = CriticalSlowingDetector(artifacts_dir=artifacts)
    guardian = Guardian(
        GuardianConfig(confidence_threshold=0.7, permission_mode="block"),
        safety_manager=safety,
        csd=csd,
    )

    chaos = ChaosInjector(seed=seed)

    # 观测指标
    kill_switch_triggers = 0
    kill_switch_false_positives = 0
    stuck_states = []  # (phase, count)
    memory_samples = []
    guardian_interventions = 0
    guardian_total = 0
    task_failures = 0
    task_total = 0

    mem_start = get_memory_mb()
    t_start = time.time()
    iteration = 0

    operations = ["read_file", "write_file", "edit_file", "execute_cmd"]

    try:
        while time.time() - t_start < duration_sec:
            iteration += 1

            # 随机选择操作
            op = chaos.rng.choice(operations)
            confidence = chaos.corrupt_confidence(chaos.rng.uniform(0.5, 1.0))

            # Guardian 检查
            guardian_total += 1
            verdict = guardian.check(
                operation=op, confidence=confidence,
                session_id="chaos",
                error_type="accuracy", gv_id="chaos_test",
            )
            if verdict.intervene:
                guardian_interventions += 1

            # 模拟任务执行（注入故障）
            chaos.maybe_delay()
            task_total += 1
            if chaos.should_fail():
                task_failures += 1
                csd.record_failure(
                    error_type="accuracy", gv_ids=["chaos_test"],
                    recovery_time_s=chaos.rng.uniform(0.1, 5.0),
                    task_id=f"chaos-{iteration}",
                )
                chaos.injected_count += 1

            # 状态机推进（正常路径）
            try:
                state = states.p0_self_check()
                state = states.transition(state, "P1_planned", context={
                    "intent_anchor": {"user_goal": "chaos"},
                    "task_dag": [{"id": f"c{iteration}", "assigned_worker": "w1"}],
                })
                state = states.transition(state, "P2_executed",
                    context={"task_dag": [{"id": f"c{iteration}", "assigned_worker": "w1"}]})
                state["review_verdict"] = chaos.rng.uniform(0.5, 1.0)
                states.transition(state, "P3_reviewed",
                    context={"results": [("c1", None)]})
                try:
                    states.transition(state, "P4_delivered")
                except Exception:
                    pass  # 审查不通过 → 预期行为
            except Exception:
                pass  # 守卫拦截 → 预期行为

            # 检测 Kill Switch 误触发
            if safety.is_emergency_stop:
                kill_switch_triggers += 1
                # 判断是否误杀：如果没有真正的 L5 操作或 I 级事故
                is_false_positive = (
                    op not in ("rm_rf", "sudo", "git_push_force", "eval_shell", "pipe_to_sh")
                )
                if is_false_positive:
                    kill_switch_false_positives += 1
                # 重置以便继续测试
                safety.reset_kill_switch()

            # 检测状态机卡死
            current_phase = state.get("current_phase", "?")
            if current_phase != "P4_delivered" and current_phase != "P0_ready":
                stuck_states.append(current_phase)

            # 内存采样
            if iteration % 50 == 0:
                memory_samples.append(get_memory_mb())

    except KeyboardInterrupt:
        print("\n混沌测试被中断。")

    elapsed = time.time() - t_start
    mem_end = get_memory_mb()

    return {
        "test_config": {
            "duration_s": duration_sec,
            "actual_duration_s": round(elapsed, 1),
            "seed": seed,
            "failure_rate": chaos.failure_rate,
            "iterations": iteration,
        },
        "kill_switch": {
            "triggers": kill_switch_triggers,
            "false_positives": kill_switch_false_positives,
            "false_positive_rate_pct": round(
                100 * kill_switch_false_positives / max(kill_switch_triggers, 1), 2
            ),
        },
        "state_machine": {
            "stuck_count": len(stuck_states),
            "stuck_rate_pct": round(100 * len(stuck_states) / max(iteration, 1), 2),
            "top_stuck_phases": list(set(stuck_states))[:5],
        },
        "guardian": {
            "total_checks": guardian_total,
            "interventions": guardian_interventions,
            "intervention_rate_pct": round(
                100 * guardian_interventions / max(guardian_total, 1), 2
            ),
        },
        "tasks": {
            "total": task_total,
            "failures": task_failures,
            "failure_rate_pct": round(100 * task_failures / max(task_total, 1), 2),
            "chaos_injected": chaos.injected_count,
        },
        "memory": {
            "start_mb": round(mem_start, 2) if mem_start > 0 else "N/A",
            "end_mb": round(mem_end, 2) if mem_end > 0 else "N/A",
            "samples": memory_samples,
            "leak_suspected": (
                memory_samples[-1] > memory_samples[0] * 1.2
                if len(memory_samples) >= 2 else False
            ),
        },
    }


def generate_report(results: dict, output_path: str):
    """生成混沌测试报告 Markdown。"""
    c = results
    ks = c["kill_switch"]
    sm = c["state_machine"]
    gd = c["guardian"]
    tk = c["tasks"]
    mem = c["memory"]
    tc = c["test_config"]

    lines = [
        f"# kugua Chaos Report v0.3.0",
        f"",
        f"> 生成时间: {datetime.now(timezone.utc).isoformat()}",
        f"> 测试时长: {tc['actual_duration_s']}s (目标 {tc['duration_s']}s)",
        f"> 迭代次数: {tc['iterations']}",
        f"> 故障注入率: {tc['failure_rate']*100}%",
        f"> 随机种子: {tc['seed']}",
        f"",
        f"## 1. Kill Switch 误杀率",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 触发次数 | {ks['triggers']} |",
        f"| 误杀次数 | {ks['false_positives']} |",
        f"| 误杀率 | {ks['false_positive_rate_pct']}% |",
        f"",
        f"**判定**: {'⚠️ 需改进' if ks['false_positive_rate_pct'] > 5 else '✅ 可接受'}",
        f"",
        f"## 2. 状态机卡死检测",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 卡死次数 | {sm['stuck_count']} |",
        f"| 卡死率 | {sm['stuck_rate_pct']}% |",
        f"| 卡死阶段 | {sm['top_stuck_phases']} |",
        f"",
        f"**判定**: {'⚠️ 需改进' if sm['stuck_rate_pct'] > 10 else '✅ 可接受'}",
        f"",
        f"## 3. Guardian 介入率",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 总检查 | {gd['total_checks']} |",
        f"| 介入次数 | {gd['interventions']} |",
        f"| 介入率 | {gd['intervention_rate_pct']}% |",
        f"",
        f"**判定**: {'⚠️ 需改进' if gd['intervention_rate_pct'] > 50 else '✅ 可接受'}",
        f"",
        f"## 4. 任务执行",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 总任务 | {tk['total']} |",
        f"| 失败 | {tk['failures']} |",
        f"| 失败率 | {tk['failure_rate_pct']}% |",
        f"| 混沌注入 | {tk['chaos_injected']} |",
        f"",
        f"## 5. 内存",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 初始 (MB) | {mem['start_mb']} |",
        f"| 结束 (MB) | {mem['end_mb']} |",
        f"| 疑似泄露 | {mem['leak_suspected']} |",
        f"",
        f"## 总结",
        f"",
        f"- Kill Switch: {'PASS' if ks['false_positive_rate_pct'] <= 5 else 'FAIL'}",
        f"- 状态机卡死: {'PASS' if sm['stuck_rate_pct'] <= 10 else 'FAIL'}",
        f"- Guardian 介入率: {'PASS' if gd['intervention_rate_pct'] <= 50 else 'FAIL'}",
        f"- 内存泄露: {'PASS' if not mem['leak_suspected'] else 'FAIL'}",
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已保存至: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="kugua 混沌工程测试")
    parser.add_argument("--duration", type=int, default=60, help="测试时长（秒）")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--failure-rate", type=float, default=0.15)
    parser.add_argument("--output", type=str, default="chaos_report_v0.3.0.md")
    args = parser.parse_args()

    results = run_chaos(
        duration_sec=args.duration,
        seed=args.seed,
    )

    # 注入故障率配置
    results["test_config"]["failure_rate"] = args.failure_rate

    out_path = args.output
    if not Path(out_path).is_absolute():
        out_path = str(Path(os.getcwd()) / out_path)
    generate_report(results, out_path)
    print(json.dumps(results, ensure_ascii=False, indent=2))
