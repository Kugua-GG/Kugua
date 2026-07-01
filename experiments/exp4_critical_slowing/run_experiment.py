"""
实验 4: 临界慢化预测能力 — 可重复对照实验
═══════════════════════════════════════════════════════════

场景: Agent 执行渐进复杂化任务 (上下文长度指数增长: 100→200→400→...→25600)。
      性能在某个临界点后崩塌。CSD 通过 Mann-Kendall 检测恢复时间趋势。

核心问题: CSD 能否在性能实际崩塌**之前**发出预警？

指标:
  - 预警提前量 (timesteps): CSD首次critical vs 性能首次跌破阈值
  - 预警准确率: CSD critical后性能是否真的崩塌
  - 误报率: CSD critical但性能未崩塌

用法:
  python experiments/exp4_critical_slowing/run_experiment.py --steps 50 --seed 42
"""
import sys, os, json, csv, random, math, argparse
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "kugua-code"))
from kugua.critical_slowing import CriticalSlowingDetector, mann_kendall

# ═══════════════════════════════════════════════════════════════
# 渐进复杂化任务模拟器
# ═══════════════════════════════════════════════════════════════

class ProgressiveComplexitySimulator:
    """模拟一个随上下文增长而性能退化的 Agent。

    恢复时间模型:
      - 正常阶段 (step < tipping_point * 0.7): 稳定 ~0.5s + 噪声
      - 预临界阶段 (0.7-1.0 × tipping): 方差增大, 均值缓慢上升
      - 临界后 (>= tipping): 指数增长 → 性能崩塌
    """

    def __init__(self, seed: int = 42, tipping_point: int = 35):
        self.rng = random.Random(seed)
        self.tipping = tipping_point  # 性能崩塌的临界点
        self.step = 0
        self.recovery_times: List[float] = []
        self.performance: List[float] = []  # 0-1, 越高越好
        self.context_sizes: List[int] = []

    def run_step(self) -> Tuple[float, float, int]:
        """执行一步。返回 (recovery_time_s, performance_score, context_size)。"""
        self.step += 1
        ctx = 100 * (2 ** min(self.step // 5, 8))  # 指数增长到25600后稳定
        self.context_sizes.append(ctx)

        # 恢复时间: 临界慢化的核心信号
        if self.step < self.tipping * 0.7:
            # 正常阶段
            recovery = 0.5 + self.rng.gauss(0, 0.1)
            perf = 0.95 + self.rng.gauss(0, 0.02)
        elif self.step < self.tipping:
            # 预临界: 方差增大 (临界慢化的特征)
            noise = self.rng.gauss(0, 0.3)  # 增大方差
            recovery = 0.6 + (self.step - self.tipping * 0.7) * 0.02 + noise
            perf = 0.9 - (self.step - self.tipping * 0.7) * 0.01 + self.rng.gauss(0, 0.05)
        else:
            # 临界后: 指数恶化
            steps_past = self.step - self.tipping + 1
            recovery = 1.0 * math.exp(steps_past * 0.15) + self.rng.gauss(0, 0.5)
            perf = max(0.1, 0.8 * math.exp(-steps_past * 0.2))

        recovery = max(0.1, recovery)
        perf = max(0, min(1, perf))

        self.recovery_times.append(recovery)
        self.performance.append(perf)
        return recovery, perf, ctx


# ═══════════════════════════════════════════════════════════════
# 分析
# ═══════════════════════════════════════════════════════════════

@dataclass
class StepRecord:
    step: int; recovery_s: float; performance: float; context_size: int
    csd_critical: bool = False; csd_pvalue: float = 1.0; csd_tau: float = 0.0


def run_experiment(steps: int = 50, tipping: int = 35, seed: int = 42):
    sim = ProgressiveComplexitySimulator(seed=seed, tipping_point=tipping)
    csd = CriticalSlowingDetector(
        artifacts_dir=Path(os.getenv("KUGUA_ARTIFACTS_DIR",
            str(Path.home() / ".claude" / ".codex" / "artifacts"))) / "exp4",
        min_samples=5,
    )

    records: List[StepRecord] = []
    first_csd_warning = -1
    first_perf_drop = -1
    perf_threshold = 0.7

    for i in range(steps):
        recovery, perf, ctx = sim.run_step()

        # 每次执行后记录到 CSD
        csd.record_failure(
            error_type="accuracy", gv_ids=["progressive_task"],
            recovery_time_s=recovery, task_id=f"step-{i}",
        )

        # 检测临界慢化
        signal = csd.detect("accuracy", "progressive_task")
        rec = StepRecord(
            step=i, recovery_s=round(recovery, 4),
            performance=round(perf, 4), context_size=ctx,
            csd_critical=signal.critical,
            csd_pvalue=round(signal.p_value, 6),
            csd_tau=round(signal.kendall_tau, 4),
        )
        records.append(rec)

        # 记录首次预警和首次性能崩塌
        if signal.critical and first_csd_warning < 0:
            first_csd_warning = i
        if perf < perf_threshold and first_perf_drop < 0:
            first_perf_drop = i

    # 计算预警提前量
    if first_csd_warning >= 0 and first_perf_drop >= 0:
        lead_time = first_perf_drop - first_csd_warning
    elif first_csd_warning >= 0:
        lead_time = steps - first_csd_warning  # 性能未崩塌但预警了
    else:
        lead_time = -1  # 未预警

    # 误报: CSD critical 但性能从未跌破阈值
    false_alarms = 0
    true_alarms = 0
    for r in records:
        if r.csd_critical:
            # 检查此后3步内性能是否跌破阈值
            future_perfs = [rr.performance for rr in records[r.step:min(r.step+4, len(records))]]
            if any(p < perf_threshold for p in future_perfs):
                true_alarms += 1
            else:
                false_alarms += 1

    total_alarms = true_alarms + false_alarms
    false_alarm_rate = false_alarms / max(total_alarms, 1)

    return {
        "config": {"steps": steps, "tipping_point": tipping, "seed": seed,
                   "perf_threshold": perf_threshold},
        "early_warning": {
            "first_csd_warning_step": first_csd_warning,
            "first_perf_drop_step": first_perf_drop,
            "lead_time_steps": lead_time,
            "lead_time_pct": round(100 * lead_time / max(steps, 1), 1),
            "warned_before_collapse": lead_time > 0,
        },
        "accuracy": {
            "true_alarms": true_alarms,
            "false_alarms": false_alarms,
            "false_alarm_rate": round(false_alarm_rate, 4),
            "total_alarms": total_alarms,
        },
        "records": records,
    }


def generate_report(result: Dict, output_dir: Path):
    ew = result["early_warning"]
    acc = result["accuracy"]
    cfg = result["config"]

    lines = [
        f"# 实验 4 报告: 临界慢化预测能力",
        f"",
        f"> 生成: {datetime.now(timezone.utc).isoformat()}",
        f"> 配置: {cfg['steps']}步, 临界点={cfg['tipping_point']}, 性能阈值={cfg['perf_threshold']}",
        f"",
        f"## 1. 预警提前量（核心指标）",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| CSD 首次预警 | 步骤 {ew['first_csd_warning_step']} |",
        f"| 性能首次跌破 {cfg['perf_threshold']} | 步骤 {ew['first_perf_drop_step']} |",
        f"| **预警提前量** | **{ew['lead_time_steps']} 步 ({ew['lead_time_pct']}%)** |",
        f"| 崩塌前预警 | {'✅ 是' if ew['warned_before_collapse'] else '❌ 否'} |",
        f"",
        f"## 2. 预警准确率",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 真正预警 (后续确实崩塌) | {acc['true_alarms']} |",
        f"| 误报 (预警后未崩塌) | {acc['false_alarms']} |",
        f"| 误报率 | {acc['false_alarm_rate']*100:.1f}% |",
        f"",
        f"## 3. 结论",
        f"",
    ]

    if ew['warned_before_collapse'] and acc['false_alarm_rate'] < 0.3:
        lines.append(f"- ✅ CSD 在性能崩塌前 {ew['lead_time_steps']} 步发出预警")
        lines.append(f"- 误报率 {acc['false_alarm_rate']*100:.1f}% 在可接受范围")
    elif ew['warned_before_collapse']:
        lines.append(f"- ⚠️ CSD 提前 {ew['lead_time_steps']} 步预警, 但误报率 {acc['false_alarm_rate']*100:.1f}% 偏高")
    else:
        lines.append(f"- ❌ CSD 未在性能崩塌前发出预警 (或同时发生)")

    # 时间序列附录
    lines.extend([
        f"",
        f"## 附录: 恢复时间序列",
        f"",
        f"| 步骤 | 恢复时间(s) | 性能 | CSD critical | p-value |",
        f"|---|---|---|---|---|",
    ])
    for r in result["records"]:
        if r.csd_critical or r.step % 5 == 0:
            flag = "⚠️" if r.csd_critical else ""
            lines.append(
                f"| {r.step} | {r.recovery_s:.3f} | {r.performance:.3f} | "
                f"{flag} | {r.csd_pvalue:.4f} |"
            )

    report_path = output_dir / "report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告: {report_path}")


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="实验4: 临界慢化预测能力")
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--tipping", type=int, default=35, help="性能崩塌临界点")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"实验 4: 临界慢化预测 — {args.steps}步, 临界点={args.tipping}")
    print("=" * 60)

    result = run_experiment(steps=args.steps, tipping=args.tipping, seed=args.seed)

    # CSV
    csv_path = output_dir / "raw_data.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "step", "recovery_s", "performance", "context_size",
            "csd_critical", "csd_pvalue", "csd_tau",
        ])
        writer.writeheader()
        for r in result["records"]:
            writer.writerow({
                "step": r.step, "recovery_s": r.recovery_s,
                "performance": r.performance, "context_size": r.context_size,
                "csd_critical": r.csd_critical, "csd_pvalue": r.csd_pvalue,
                "csd_tau": r.csd_tau,
            })
    print(f"原始数据: {csv_path}")

    summary_path = output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in result.items() if k != "records"},
                  f, ensure_ascii=False, indent=2)
    print(f"汇总: {summary_path}")

    generate_report(result, output_dir)

    ew = result["early_warning"]
    acc = result["accuracy"]
    print(f"\n{'='*60}")
    print(f"预警提前: {ew['lead_time_steps']}步 ({ew['lead_time_pct']}%) "
          f"({'[PASS] 崩塌前预警' if ew['warned_before_collapse'] else '[FAIL] 未预警'})")
    print(f"误报率: {acc['false_alarm_rate']*100:.1f}% ({acc['false_alarms']}/{acc['total_alarms']})")


if __name__ == "__main__":
    main()
