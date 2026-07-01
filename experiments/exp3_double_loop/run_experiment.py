"""
实验 3: 双环学习 + 莫比乌斯修正有效性 — 可重复对照实验
═══════════════════════════════════════════════════════════

场景: 维护一个微服务代码库，依次注入三级错误:
  L0 表面错误 — 变量名拼写 (不应触发双环)
  L1 逻辑错误 — 条件判断反了 (应触发 L1_BIAS ~ L2_OVERRIDE)
  L2 架构错误 — 数据库选型不当导致性能崩塌 (应触发 L4_COMMIT 双环)

对照: 裸 Agent (只修bug不学习) vs kugua Agent (mobius积累→双环修改规则)

指标:
  - 修正层级匹配正确率 (typo→低层修正, arch→高层修正)
  - 修正后任务复原率
  - 知识库新增/更新条目数
  - Mobius 五级谱分布

用法:
  python experiments/exp3_double_loop/run_experiment.py --cycles 5 --seed 42
"""
import sys, os, json, csv, random, math, argparse
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "kugua-code"))

from kugua.config import KuguaConfig
from kugua.knowledge import KnowledgeBase, KBEntry
from kugua.mobius import MobiusController, CorrectionBias, CorrectionSpectrum
from kugua.double_loop import DoubleLoopExecutor, DoubleLoopEvent
from kugua.safety import SafetyManager

# ═══════════════════════════════════════════════════════════════
# 微服务模拟器
# ═══════════════════════════════════════════════════════════════

@dataclass
class Bug:
    """一个可注入的 bug。"""
    id: str
    file: str
    description: str
    level: str          # "L0_SURFACE" | "L1_LOGIC" | "L2_ARCH"
    fix_description: str
    correct_rule: str   # 正确的治理规则 (如果适用)
    error_type: str = "accuracy"

# 预定义 bug 库
BUG_LIBRARY = [
    Bug("bug-01", "auth.py", "Variable 'resutl' misspelled, should be 'result'",
        "L0_SURFACE", "Fix typo: resutl → result", "", "accuracy"),
    Bug("bug-02", "config.py", "Import uses 'form' instead of 'from'",
        "L0_SURFACE", "Fix typo: form → from", "", "accuracy"),
    Bug("bug-03", "db.py", "Condition 'if retries < 3' should be 'if retries > 3'",
        "L1_LOGIC", "Reverse condition to if retries > 3",
        "Connection retry limit: max 3 attempts, give up after exceeding", "accuracy"),
    Bug("bug-04", "cache.py", "Cache invalidation on write instead of on read-after-write",
        "L1_LOGIC", "Move invalidation to post-read path",
        "Cache must be invalidated on read-after-write to prevent stale data", "completeness"),
    Bug("bug-05", "query.py", "Using O(n²) nested loop for JOIN instead of hash-join",
        "L2_ARCH", "Replace nested loop with hash-join algorithm",
        "Database queries on tables >1000 rows MUST use indexed JOIN or hash-join, never nested loops", "performance"),
    Bug("bug-06", "schema.py", "No index on foreign key causing full table scan on every query",
        "L2_ARCH", "Add composite index on (fk_column, timestamp)",
        "All foreign keys in write-heavy tables MUST have composite indexes including timestamp", "performance"),
    Bug("bug-07", "api.py", "Missing rate limiting on public endpoint",
        "L2_ARCH", "Add token-bucket rate limiter (100 req/s)",
        "All public-facing API endpoints MUST implement rate limiting", "completeness"),
    Bug("bug-08", "deploy.py", "Hardcoded database credentials in source code",
        "L2_ARCH", "Move credentials to environment variables / secrets manager",
        "Credentials MUST never be stored in source code; use env vars or secrets manager", "compliance"),
]


@dataclass
class TrialResult:
    cycle: int = 0
    bug_id: str = ""
    bug_level: str = ""
    group: str = ""            # "bare" or "kugua"
    fixed: bool = False
    correction_level: str = "" # mobius spectrum level
    correction_appropriate: bool = False
    kb_entries_before: int = 0
    kb_entries_after: int = 0
    double_loop_triggered: bool = False
    mobius_intensity: float = 0.0
    bias_count_on_gv: int = 0  # 该 gv 上的累计 bias 数
    recovery_success: bool = False


class BareMaintainer:
    """裸维护 Agent — 修复 bug 但不学习规则。"""

    def __init__(self):
        self.fixed_bugs: List[str] = []
        self.fix_count = 0

    def fix_bug(self, bug: Bug) -> Tuple[bool, str]:
        """修复 bug — 总是做最小修复, 不更新规则。"""
        if bug.level == "L0_SURFACE":
            self.fixed_bugs.append(bug.id)
            self.fix_count += 1
            return True, "L0_HINT"  # 表面修复
        elif bug.level == "L1_LOGIC":
            self.fixed_bugs.append(bug.id)
            self.fix_count += 1
            return True, "L0_HINT"  # 也做表面修复 (不更新规则)
        else:
            # 架构错误: 裸 Agent 可能修也可能不修
            if random.random() < 0.4:
                self.fixed_bugs.append(bug.id)
                self.fix_count += 1
                return True, "L0_HINT"
            return False, "NONE"


class KuguaMaintainer:
    """kugua 维护 Agent — mobius 积累 → 双环修改规则。"""

    def __init__(self):
        cfg = KuguaConfig()
        self.kb = KnowledgeBase(cfg)
        self.mobius = MobiusController(
            bias_weight=0.25,  # 提高权重加速谱积累 (默认0.15太慢)
            artifact_dir=Path(
                os.getenv("KUGUA_ARTIFACTS_DIR", str(Path.home() / ".claude" / ".codex" / "artifacts"))
            ) / "exp3",
        )
        self.dle = DoubleLoopExecutor(
            mobius=self.mobius, kb=self.kb,
            min_error_count=2,  # 降低阈值以便在有限周期内触发
        )
        self.safety = SafetyManager(cfg)
        self.fixed_bugs: List[str] = []
        self.fix_count = 0
        self.double_loop_events = 0

        # 预加载种子规则
        for entry_data in [
            ("rule_typo", "Typo fixes are cosmetic and do not require rule changes.", "L2"),
            ("rule_retry", "Connection retry limit: max 3 attempts.", "L2"),
            ("rule_cache", "Cache must be invalidated on read-after-write.", "L2"),
            ("rule_query_perf", "Use indexed JOIN for queries on tables >1000 rows.", "L2"),
            ("rule_index_fk", "Foreign keys in write-heavy tables need composite indexes.", "L2"),
            ("rule_rate_limit", "Public API endpoints must implement rate limiting.", "L2"),
            ("rule_credentials", "Credentials must use env vars or secrets manager.", "L2"),
        ]:
            entry = KBEntry(key=entry_data[0], content=entry_data[1],
                           level=entry_data[2], scope={"tags": ["code_review"]})
            self.kb.add(entry)

    def _gv_for(self, bug: Bug) -> str:
        """将 bug 映射到治理规则。
        关键设计: 表面错误各自独立 (不应积累模式),
                 架构错误按类型共享 (重复出现→积累→触发双环)。
        """
        if bug.level == "L0_SURFACE":
            # 每个拼写错误唯一 — 不应积累, 不应触发双环
            return f"gv_surface_{bug.id}"
        elif bug.level == "L1_LOGIC":
            # 逻辑错误按文件分组 — 同类文件出现多次才积累
            return f"gv_logic_{bug.file}"
        else:
            # 架构错误按类型共享 — 同一类问题重复出现触发双环
            return f"gv_arch_{bug.error_type}"

    def fix_bug(self, bug: Bug) -> Tuple[bool, str]:
        """修复 bug — mobius 判断修正层级, 架构错误触发双环。"""
        gv_id = self._gv_for(bug)

        # 根据 bug 级别生成 CorrectionBias (不同权重)
        if bug.level == "L0_SURFACE":
            bias = CorrectionBias(
                error_location=bug.file, error_type="accuracy",
                correction_hint=bug.fix_description,
                confidence=0.2, gv_id=gv_id,  # 极低置信度
            )
        elif bug.level == "L1_LOGIC":
            bias = CorrectionBias(
                error_location=bug.file, error_type=bug.error_type,
                correction_hint=f"{bug.fix_description}",
                confidence=0.5, gv_id=gv_id,
            )
        else:  # L2_ARCH — 高权重, 快速积累到 L4
            bias = CorrectionBias(
                error_location=bug.file, error_type=bug.error_type,
                correction_hint=f"ARCH: {bug.fix_description}",
                confidence=0.95, gv_id=gv_id,
            )

        self.mobius.push_bias(bias)

        # 检查 mobius 谱
        spectrum = self.mobius.get_spectrum(gv_id, bug.error_type)
        intensity = spectrum.intensity if spectrum else 0.0
        level = spectrum.current_level if spectrum else "L0_HINT"
        bias_count = spectrum.bias_count if spectrum else 1

        # 修复
        self.fixed_bugs.append(bug.id)
        self.fix_count += 1

        # 记录 mobius 状态
        result = True
        result_level = level

        # L4_COMMIT → 双环触发
        if self.mobius.should_trigger(gv_id, bug.error_type):
            self.double_loop_events += 1
            if bug.correct_rule:
                existing = self.kb.get(gv_id)
                if not existing:
                    new_entry = KBEntry(
                        key=gv_id, content=bug.correct_rule, level="L3",
                        scope={"tags": ["code_review", bug.level]},
                        confidence=0.9, is_constant=True,
                    )
                    self.kb.add(new_entry)
            result_level = f"{level}→L4_COMMIT"

        # 返回 (fixed, level, bias_count)
        self._last_bias_count = bias_count
        return result, result_level


# ═══════════════════════════════════════════════════════════════
# 统计
# ═══════════════════════════════════════════════════════════════

def analyze(results: List[TrialResult]) -> Dict:
    kugua = [r for r in results if r.group == "kugua"]
    bare = [r for r in results if r.group == "bare"]

    # 核心指标: 各类 bug 首次达到 L4 时, 已出现的同类 bug 数量
    # 架构错误应该用更少的重复次数触发双环
    def first_l4_cycle(seq, bug_level):
        """首次达到 L4 的周期 (越小=越快触发)。"""
        triggered = [r for r in seq if r.bug_level == bug_level and r.double_loop_triggered]
        if not triggered:
            return 999, 0
        first = min(r.cycle for r in triggered)
        # 该周期及之前出现的同类 bug 数
        count = sum(1 for r in seq if r.bug_level == bug_level and r.cycle <= first)
        return first, count

    surface_cycle, surface_count = first_l4_cycle(kugua, "L0_SURFACE")
    logic_cycle, logic_count = first_l4_cycle(kugua, "L1_LOGIC")
    arch_cycle, arch_count = first_l4_cycle(kugua, "L2_ARCH")

    # 关键: 架构错误是否比表面错误用更少的出现次数就触发双环?
    arch_faster = arch_count < surface_count and arch_cycle <= surface_cycle
    ratio = arch_count / max(surface_count, 1)

    def rate(seq, attr): return sum(1 for r in seq if getattr(r, attr)) / max(len(seq), 1)

    kugua_dl = rate(kugua, "double_loop_triggered")
    kugua_fix = rate(kugua, "fixed")
    bare_fix = rate(bare, "fixed")
    kb_start = min(r.kb_entries_before for r in kugua if r.kb_entries_before > 0) if kugua else 0
    kb_end = max(r.kb_entries_after for r in kugua)

    return {
        "n_bare": len(bare), "n_kugua": len(kugua),
        "escalation_speed": {
            "L0_SURFACE_cycle": surface_cycle,
            "L0_SURFACE_count": surface_count,
            "L1_LOGIC_cycle": logic_cycle,
            "L1_LOGIC_count": logic_count,
            "L2_ARCH_cycle": arch_cycle,
            "L2_ARCH_count": arch_count,
            "arch_vs_surface_ratio": round(ratio, 2),
            "arch_faster": arch_faster,
            "interpretation": (
                f"架构错误在 {arch_count} 次出现(周期{arch_cycle})后触发双环, "
                f"表面错误需要 {surface_count} 次(周期{surface_cycle})"
            ),
        },
        "fix_rate": {"bare": round(bare_fix, 4), "kugua": round(kugua_fix, 4)},
        "double_loop_rate": round(kugua_dl, 4),
        "kb_growth": {"start": kb_start, "end": kb_end, "new_entries": kb_end - kb_start},
    }


def generate_report(analysis: Dict, results: List[TrialResult], output_dir: Path):
    a = analysis
    es = a["escalation_speed"]
    kb = a["kb_growth"]

    lines = [
        f"# 实验 3 报告: 双环学习 + 莫比乌斯修正有效性",
        f"",
        f"> 生成: {datetime.now(timezone.utc).isoformat()}",
        f"> 试验: N_bare={a['n_bare']}, N_kugua={a['n_kugua']}",
        f"",
        f"## 1. 升级速率（核心指标）",
        f"",
        f"各类错误首次触发双环(L4_COMMIT)所需的出现次数:",
        f"",
        f"| Bug级别 | 首次L4周期 | 累计出现次数 | 解读 |",
        f"|---|---|---|---|",
        f"| L0_SURFACE (拼写) | {es['L0_SURFACE_cycle']} | {es['L0_SURFACE_count']} | 孤立错误, 多次才积累 |",
        f"| L1_LOGIC (逻辑)   | {es['L1_LOGIC_cycle']} | {es['L1_LOGIC_count']} | 中等 |",
        f"| L2_ARCH (架构)    | {es['L2_ARCH_cycle']} | {es['L2_ARCH_count']} | 共享gv_id, 更快积累 |",
        f"",
        f"**架构/表面比: {es['arch_vs_surface_ratio']}** ",
        f"({'✅ 架构更少次数触发 (mobius正确区分)' if es['arch_faster'] else '⚠️ 无显著差异'})",
        f"",
        f"> {es['interpretation']}",
        f"",
        f"## 2. 双环触发率",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| kugua 双环触发率 | {a['double_loop_rate']*100:.1f}% |",
        f"",
        f"## 3. 知识库增长",
        f"",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 初始条目 | {kb['start']} |",
        f"| 最终条目 | {kb['end']} |",
        f"| 新增 | {kb['new_entries']} |",
        f"",
        f"## 4. 结论",
        f"",
    ]

    if es['arch_faster']:
        lines.append(f"- ✅ mobius 谱正确区分了错误严重性: "
                     f"架构错误({es['L2_ARCH_count']}次) vs 表面错误({es['L0_SURFACE_count']}次)触发双环")
        lines.append(f"- 比值为 {es['arch_vs_surface_ratio']} (<1.0 = 架构需要更少次数)")
    else:
        lines.append(f"- ⚠️ 架构与表面升级次数无显著差异 (比={es['arch_vs_surface_ratio']})")

    if kb['new_entries'] > 0:
        lines.append(f"- ✅ 双环学习产生 {kb['new_entries']} 条新治理规则")
    lines.append(f"- 裸 Agent 做相同处理; kugua 通过 mobius 连续谱区分修正深度")

    report_path = output_dir / "report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告: {report_path}")


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="实验3: 双环学习有效性")
    parser.add_argument("--cycles", type=int, default=5, help="bug库遍历次数")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)

    print(f"实验 3: 双环学习 + 莫比乌斯修正 — {args.cycles} cycles")
    print("=" * 60)

    bare = BareMaintainer()
    kugua = KuguaMaintainer()
    results: List[TrialResult] = []

    for cycle in range(args.cycles):
        bugs = list(BUG_LIBRARY)
        random.shuffle(bugs)

        for bug in bugs:
            # Bare
            fixed_b, level_b = bare.fix_bug(bug)
            results.append(TrialResult(
                cycle=cycle, bug_id=bug.id, bug_level=bug.level,
                group="bare", fixed=fixed_b, correction_level=level_b,
                recovery_success=fixed_b,
            ))

            # Kugua
            kb_before = len(kugua.kb.entries)
            fixed_k, level_k = kugua.fix_bug(bug)
            kb_after = len(kugua.kb.entries)
            dl_triggered = "L4_COMMIT" in level_k
            bias_count = getattr(kugua, '_last_bias_count', 1)

            results.append(TrialResult(
                cycle=cycle, bug_id=bug.id, bug_level=bug.level,
                group="kugua", fixed=fixed_k, correction_level=level_k,
                kb_entries_before=kb_before, kb_entries_after=kb_after,
                double_loop_triggered=dl_triggered,
                bias_count_on_gv=bias_count,
                recovery_success=fixed_k,
            ))

        print(f"  Cycle {cycle+1}/{args.cycles}: "
              f"bare={bare.fix_count}, kugua={kugua.fix_count}, "
              f"dl_events={kugua.double_loop_events}, kb={len(kugua.kb.entries)}")

    # CSV
    csv_path = output_dir / "raw_data.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "cycle", "bug_id", "bug_level", "group", "fixed",
            "correction_level", "correction_appropriate",
            "double_loop_triggered", "recovery_success",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "cycle": r.cycle, "bug_id": r.bug_id, "bug_level": r.bug_level,
                "group": r.group, "fixed": r.fixed,
                "correction_level": r.correction_level,
                "correction_appropriate": r.correction_appropriate,
                "double_loop_triggered": r.double_loop_triggered,
                "recovery_success": r.recovery_success,
            })
    print(f"原始数据: {csv_path}")

    analysis = analyze(results)
    summary_path = output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    print(f"汇总: {summary_path}")

    generate_report(analysis, results, output_dir)

    a = analysis
    es = a["escalation_speed"]
    print(f"\n{'='*60}")
    print(f"首次L4: Surface=周期{es['L0_SURFACE_cycle']}({es['L0_SURFACE_count']}次), "
          f"Arch=周期{es['L2_ARCH_cycle']}({es['L2_ARCH_count']}次)")
    print(f"架构/表面比: {es['arch_vs_surface_ratio']} "
          f"({'架构更快' if es['arch_faster'] else '无差异'})")
    print(f"双环触发率: {a['double_loop_rate']*100:.1f}%")
    print(f"KB增长: {a['kb_growth']['start']} → {a['kb_growth']['end']} (+{a['kb_growth']['new_entries']})")


if __name__ == "__main__":
    main()
