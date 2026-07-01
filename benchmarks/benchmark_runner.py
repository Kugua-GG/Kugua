"""
kugua Guardian — 性能基准套件 v0.3.0

测量认知监护模式的核心性能指标:
  - 延迟百分位 (P50/P95/P99)
  - 内存占用 (RSS)
  - 每决策步 LLM 调用次数
  - 吞吐量 (checks/sec)

Usage:
    python benchmarks/benchmark_runner.py --iterations 1000
    python benchmarks/benchmark_runner.py --scenario stress --iterations 5000
"""
import json
import os
import sys
import time
import random
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kugua-code"))

from kugua.config import KuguaConfig
from kugua.safety import SafetyManager
from kugua.critical_slowing import CriticalSlowingDetector
from kugua.guardian import Guardian, GuardianConfig


def get_memory_mb():
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return -1


def run_benchmark(iterations: int = 1000, scenario: str = "normal",
                  deep_review: bool = False):
    """运行性能基准测试。"""
    print(f"kugua Guardian Benchmark v0.3.0")
    print(f"  场景: {scenario} | 迭代: {iterations} | 深度审查: {deep_review}")
    print("=" * 60)

    artifacts = Path(os.getenv(
        "KUGUA_ARTIFACTS_DIR",
        str(Path.home() / ".claude" / ".codex" / "artifacts")
    ))
    artifacts.mkdir(parents=True, exist_ok=True)

    cfg = KuguaConfig()
    safety = SafetyManager(cfg)
    csd = CriticalSlowingDetector(artifacts_dir=artifacts)

    guardian_cfg = GuardianConfig(
        confidence_threshold=0.7,
        permission_mode="block",
        enable_deep_review=deep_review,
        artifacts_dir=artifacts,
    )

    guardian = Guardian(
        config=guardian_cfg,
        safety_manager=safety,
        csd=csd,
    )

    mem_before = get_memory_mb()

    # 场景定义
    scenarios = {
        "normal": {
            "ops": ["read_file"]*40 + ["write_file"]*30 + ["edit_file"]*20 + ["execute_cmd"]*10,
            "conf": (0.6, 1.0),
        },
        "high_confidence": {
            "ops": ["read_file"]*60 + ["write_file"]*30 + ["edit_file"]*10,
            "conf": (0.8, 1.0),
        },
        "low_confidence": {
            "ops": ["read_file"]*20 + ["write_file"]*30 + ["execute_cmd"]*50,
            "conf": (0.3, 0.7),
        },
        "stress": {
            "ops": ["execute_cmd"]*50 + ["delete_file"]*30 + ["write_file"]*20,
            "conf": (0.2, 0.9),
        },
    }

    sc = scenarios.get(scenario, scenarios["normal"])
    ops = sc["ops"]
    conf_min, conf_max = sc["conf"]
    random.seed(42)

    # 预热
    for _ in range(10):
        guardian.check(operation="read_file", confidence=0.9)

    # 正式测试
    t_start = time.time()
    for i in range(iterations):
        op = ops[i % len(ops)]
        conf = random.uniform(conf_min, conf_max)
        guardian.check(
            agent_output=f"bench-{i}",
            operation=op,
            confidence=conf,
            session_id="bench",
        )

    elapsed = time.time() - t_start
    mem_after = get_memory_mb()

    # 报告
    report = guardian.benchmark_report()
    result = {
        "scenario": scenario,
        "iterations": iterations,
        "duration_s": round(elapsed, 3),
        "checks_per_sec": round(iterations / max(elapsed, 0.001), 2),
        "latency_ms": {
            "avg": report["latency"]["avg_ms"],
            "p50": report["latency"]["p50_ms"],
            "p95": report["latency"]["p95_ms"],
            "p99": report["latency"]["p99_ms"],
        },
        "memory_mb": {
            "before": round(mem_before, 2),
            "after": round(mem_after, 2),
            "delta": round(mem_after - mem_before, 2),
        },
        "interventions": report["throughput"]["total_interventions"],
        "intervention_rate_pct": round(
            report["throughput"]["intervention_rate"] * 100, 2
        ),
        "deep_review_enabled": deep_review,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="kugua Guardian 性能基准")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--scenario", type=str, default="normal",
                       choices=["normal", "high_confidence", "low_confidence", "stress"])
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--deep-review", action="store_true")
    args = parser.parse_args()

    result = run_benchmark(
        iterations=args.iterations,
        scenario=args.scenario,
        deep_review=args.deep_review,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n报告已保存至: {args.output}")
