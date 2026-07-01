"""
CSD 临界点扫描 + 阶跃零延迟检测
═══════════════════════════════════════════════════════════

扫 5 种崩塌位置 (20% / 40% / 60% / 80% / 无崩塌) × 3 噪声 × 3 种子。
测量 CSD 在不同崩塌时序下的检测能力 + 阶跃崩塌的最小可检测延迟。

用法: python experiments/exp4_critical_slowing/run_tipping_sweep.py
"""
import sys, os, json, math, statistics, random
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List, Tuple, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "kugua-code"))

# 复用自适应 CSD
from run_advanced_validation import AdaptiveCSD

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════
STEPS = 250
TIPPING_FRACTIONS = [0.2, 0.4, 0.6, 0.8, 1.5]  # 1.5 = 无崩塌(临界点超出范围)
NOISE_LEVELS = {"low": 0.05, "high": 0.30}
SEEDS = [42, 123, 456]


@dataclass
class SweepResult:
    tipping_frac: float; noise: str; seed: int
    tipping_step: int
    first_warning: int
    first_drop: int
    lead: int
    far: float
    detected: bool
    zero_lag_detect: bool  # 崩塌同时或之后1步内检测


def run_sweep_trial(tipping_frac: float, noise_sigma: float, seed: int) -> SweepResult:
    rng = random.Random(seed)
    tipping = int(STEPS * tipping_frac)
    acsd = AdaptiveCSD(baseline_steps=15, warning_z=1.8, consecutive_required=2, mk_window=12)

    first_warning = -1
    first_drop = -1
    perf_history: List[float] = []
    warnings: List[int] = []

    for i in range(STEPS):
        # 模拟恢复时间: 阶跃+渐进混合
        if tipping_frac > 1.0:
            # 无崩塌: 全程稳定
            recovery = 0.5 + rng.gauss(0, noise_sigma * 0.3)
            perf = 0.93 + rng.gauss(0, 0.03)
        elif i < tipping:
            recovery = 0.5 + rng.gauss(0, noise_sigma * 0.3)
            perf = 0.93 + rng.gauss(0, 0.03)
        else:
            # 崩塌: 阶跃式瞬时退化 + 后续渐进恶化
            steps_past = i - tipping
            if steps_past == 0:
                recovery = 5.0 + rng.gauss(0, noise_sigma)
                perf = 0.25 + rng.gauss(0, 0.05)
            else:
                recovery = 5.0 * math.exp(steps_past * 0.05) + rng.gauss(0, noise_sigma)
                perf = max(0.05, 0.25 * math.exp(-steps_past * 0.1))

        recovery = max(0.1, recovery)
        perf = max(0, min(1, perf))
        perf_history.append(perf)

        warned, _, _ = acsd.feed(recovery)
        if warned:
            warnings.append(i)
            if first_warning < 0:
                first_warning = i
        if perf < 0.7 and first_drop < 0:
            first_drop = i

    # FAR
    fp = sum(1 for w in warnings
             if not any(perf_history[ww] < 0.7 for ww in range(w, min(w+4, STEPS))))
    far = round(fp / max(len(warnings), 1), 4)

    # 零延迟检测: 崩塌步或之后1步内发出预警
    zero_lag = (first_warning >= 0 and tipping_frac <= 1.0
                and first_warning <= tipping + 1)

    lead = (first_drop - first_warning) if (first_warning >= 0 and first_drop >= 0) else -1

    return SweepResult(
        tipping_frac=tipping_frac, noise=noise_sigma, seed=seed,
        tipping_step=tipping, first_warning=first_warning,
        first_drop=first_drop, lead=lead, far=far,
        detected=first_warning >= 0,
        zero_lag_detect=zero_lag,
    )


def main():
    output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(TIPPING_FRACTIONS) * len(NOISE_LEVELS) * len(SEEDS)
    print(f"临界点扫描: {len(TIPPING_FRACTIONS)}位置 × {len(NOISE_LEVELS)}噪声 × {len(SEEDS)}种子 = {total}试验")
    print("=" * 60)

    results: List[SweepResult] = []
    for tf in TIPPING_FRACTIONS:
        for nl_name, nl_sigma in NOISE_LEVELS.items():
            for seed in SEEDS:
                r = run_sweep_trial(tf, nl_sigma, seed)
                results.append(r)
                label = f"tipping={tf*100:.0f}%" if tf <= 1.0 else "no_collapse"
                print(f"  {label:14s} {nl_name:5s} seed={seed}: "
                      f"detected={r.detected} lead={r.lead} zero_lag={r.zero_lag_detect} FAR={r.far*100:.0f}%")

    # 汇总
    summary = {"total": len(results)}

    for tf in TIPPING_FRACTIONS:
        key = f"tipping_{int(tf*100)}pct" if tf <= 1.0 else "no_collapse"
        trials = [r for r in results if r.tipping_frac == tf]
        detected = sum(1 for r in trials if r.detected)
        zero_lag = sum(1 for r in trials if r.zero_lag_detect)
        leads = [r.lead for r in trials if r.lead > 0]
        fars = [r.far for r in trials]

        summary[key] = {
            "n": len(trials),
            "detected": f"{detected}/{len(trials)}",
            "zero_lag_rate": round(zero_lag / len(trials), 2) if tf <= 1.0 else "N/A",
            "lead_mean": round(statistics.mean(leads), 1) if leads else "N/A",
            "far_mean": round(statistics.mean(fars), 4) if fars else 0,
        }

    with open(output_dir / "tipping_sweep_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 终端总结
    print(f"\n{'='*60}")
    print(f"临界点扫描总结:")
    print(f"  {'位置':<12} {'检测':<8} {'零延迟率':<10} {'提前(步)':<10} {'FAR':<8}")
    for tf in TIPPING_FRACTIONS:
        key = f"tipping_{int(tf*100)}pct" if tf <= 1.0 else "no_collapse"
        s = summary[key]
        print(f"  {key:<12} {s['detected']:<8} {str(s['zero_lag_rate']):<10} "
              f"{str(s['lead_mean']):<10} {s['far_mean']*100:.0f}%")

    # 关键结论
    no_collapse = summary["no_collapse"]
    far_pct = no_collapse['far_mean']*100
    print(f"\n无崩塌场景 FAR: {far_pct:.0f}% "
          f"{'[PASS] 未误报' if no_collapse['far_mean'] < 0.05 else '[WARN] 存在误报 (' + str(no_collapse['detected']) + ')'}")


if __name__ == "__main__":
    main()
