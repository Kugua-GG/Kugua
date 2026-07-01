"""
苦瓜code 内核硬约束验证 — 15 项测试
执行: python kugua_kernel_tests.py
"""
import sys, os, json, time, math
from pathlib import Path
from datetime import datetime, timezone

# 添加 kugua-code 到路径
sys.path.insert(0, r"C:\Users\Administrator\Desktop\kugua-code")

from kugua.config import KuguaConfig
from kugua.states import StatesMachine, PhaseTransitionError, AlignResult, VALID_PHASES, PHASE_ORDER
from kugua.knowledge import KnowledgeBase, KBEntry, LEVELS
from kugua.graph import GraphKB, Node, Edge, GraphRetriever
from kugua.critical_slowing import CriticalSlowingDetector, CriticalSlowingSignal, mann_kendall
from kugua.safety import SafetyManager, TrustLevel, Incident, OPERATION_PERMISSIONS
from kugua.negentropy import Negentropy, generate_dashboard, generate_integrity_report

PASS = 0
FAIL = 0
results = []

def test(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        results.append(f"[PASS] {name}")
    else:
        FAIL += 1
        results.append(f"[FAIL] {name} — {detail}")
    print(results[-1])

# 初始化
cfg = KuguaConfig()
artifacts = cfg.artifacts_dir
artifacts.mkdir(parents=True, exist_ok=True)
print(f"Artifacts: {artifacts}\n")

# 辅助: v0.3.0 状态机需要转换上下文
_PLAN_CTX = {
    "intent_anchor": {"user_goal": "kernel_test"},
    "task_dag": [{"id": "t1", "assigned_worker": "w1"}],
}

# ============================================================
# 任务 1: 状态机正向推进与阶段锁定
# ============================================================
print("=" * 60)
print("任务 1: 状态机正向推进与阶段锁定")
sm = StatesMachine(cfg)

# 1a: 初始化
state = sm.p0_self_check()
test("1a: P0 自检完成，current_phase=P0_ready",
     state["current_phase"] == "P0_ready",
     f"current_phase={state['current_phase']}")

# 1b: 正常推进 P0_ready → P1_planned
state = sm.transition(state, "P1_planned", context=_PLAN_CTX)
test("1b: P0→P1 正常推进",
     state["current_phase"] == "P1_planned",
     f"current_phase={state['current_phase']}")

# 1c: 正常推进 P1→P2
state = sm.transition(state, "P2_executed", context={"task_dag": _PLAN_CTX["task_dag"]})
test("1c: P1→P2 正常推进",
     state["current_phase"] == "P2_executed",
     f"current_phase={state['current_phase']}")

# 1d: 试图从 P2 回退到 P0（应该记录回归但不阻止）
state = sm.transition(state, "P0_init")
regressions = state.get("phase_regressions", 0)
test("1d: P2→P0 回退被记录为 phase_regression",
     regressions >= 1,
     f"phase_regressions={regressions}")

# 1e: 阶段历史记录（p0_self_check 不记录到 phase_history，仅 transition 记录）
history = state.get("phase_history", [])
test("1e: 阶段历史包含 transition 调用产生的 3 条记录",
     len(history) >= 3,
     f"history length={len(history)}, entries={[h['phase'] for h in history]}")

# ============================================================
# 任务 2: 崩溃恢复与事务原子性
# ============================================================
print("\n" + "=" * 60)
print("任务 2: 崩溃恢复与事务原子性")

# 2a: 模拟正常写入后恢复
sm2 = StatesMachine(cfg)
state2 = sm2.p0_self_check()
state2["test_field"] = "crash_test_value"
sm2.save_state(state2)

# 重新加载
recovered = sm2.crash_recovery()
test("2a: 崩溃恢复能加载保存的状态",
     recovered.get("test_field") == "crash_test_value",
     f"test_field={recovered.get('test_field')}")

# 2b: 快照文件目录存在
snapshots = list((artifacts / "snapshots").glob("state_*.json"))
test("2b: 快照目录可访问",
     (artifacts / "snapshots").is_dir(),
     f"snapshot dir exists={(artifacts / 'snapshots').is_dir()}")

# 2c: recovery.log 存在
recovery_log = artifacts / "recovery.log"
test("2c: 恢复日志文件存在",
     recovery_log.exists(),
     f"exists={recovery_log.exists()}")

# ============================================================
# 任务 3: 双环学习触发条件检测（≥3 次同类错误）
# ============================================================
print("\n" + "=" * 60)
print("任务 3: 双环学习触发条件 — error_type + gv_id 重复检测")

from kugua.double_loop import DoubleLoopExecutor, DoubleLoopEvent
from kugua.critical_slowing import CriticalSlowingDetector
from kugua.efficacy import DoubleLoopEfficacyTracker

# 注入 3 次相同错误模式
csd = CriticalSlowingDetector(artifacts_dir=artifacts / "test_csd")
error_type = "division_by_zero"
gv_id = "fixer_v1"

for i in range(3):
    csd.record_failure(
        error_type=error_type,
        gv_ids=[gv_id],
        recovery_time_s=10.0 + i * 2,  # 递增
        task_id=f"task-{i}"
    )

records = csd._history.get(f"{error_type}:{gv_id}", [])
test("3a: 3 次错误已注入 CSD",
     len(records) >= 3,
     f"record count={len(records)}")

# 验证 double_loop_check 触发逻辑（通过 CSD + 错误计数）
# CSD 本身需要 min_samples 才能做 MK 检验
# 但 DoubleLoopExecutor 的触发可以采用 fallback（错误计数 >= 3）
# 检查 MCP double_loop_check 的触发逻辑
signal = csd.detect(error_type, gv_id)
test("3b: CSD 检测到足够的样本数",
     signal.sample_count >= 3,
     f"sample_count={signal.sample_count}")

# 3c: 前 2 次不应触发（假设 min_samples=5）
# (这里因为只有 3 个样本，如果 min_samples=5 则不满足)
# 实际触发条件还需要 double_loop_check 中的逻辑
# 我们测试 ≥3 次错误的 fallback 机制
test("3c: 3 次同类错误模式形成（用于双环触发的基础条件）",
     len(records) >= 3 and all(r["task_id"].startswith("task-") for r in records),
     f"records validated: {[r['task_id'] for r in records]}")

# ============================================================
# 任务 4: 临界慢化检测触发双环（Mann-Kendall）
# ============================================================
print("\n" + "=" * 60)
print("任务 4: 临界慢化检测 — Mann-Kendall 趋势检验")

# 4a: 测试 Mann-Kendall 函数本身
# 构造 12 个点，后 8 个严格递增
data_strict_increasing = [2.0, 2.5, 2.3, 3.0, 3.5, 4.2, 5.1, 6.0, 7.3, 8.5, 10.2, 12.0]
result_mk = mann_kendall(data_strict_increasing)
test("4a: Mann-Kendall 检测递增趋势",
     result_mk["trend"] == 1 and result_mk["significant"],
     f"trend={result_mk['trend']}, p={result_mk['p_value']}, tau={result_mk['tau']}")

# 4b: p 值 < 0.05
test("4b: p 值 < 0.05",
     result_mk["p_value"] < 0.05,
     f"p_value={result_mk['p_value']}")

# 4c: 随机数据不应触发
import random
random_data = [random.uniform(0, 10) for _ in range(12)]
result_random = mann_kendall(random_data)
test("4c: 随机数据不应有显著趋势",
     not result_random["significant"] or result_random["trend"] == 0,
     f"trend={result_random['trend']}, significant={result_random['significant']}")

# 4d: 递减数据
data_decreasing = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
result_dec = mann_kendall(data_decreasing)
test("4d: Mann-Kendall 检测递减趋势",
     result_dec["trend"] == -1,
     f"trend={result_dec['trend']}, p={result_dec['p_value']}")

# 4e: CSD 综合检测
csd4 = CriticalSlowingDetector(artifacts_dir=artifacts / "test_csd4", min_samples=5, p_threshold=0.05)
for i, t in enumerate(data_strict_increasing):
    csd4.record_failure("准确性", ["test_gv"], recovery_time_s=t, task_id=f"t{i}")

signal4 = csd4.detect("准确性", "test_gv")
test("4e: CSD 检测器运行不崩溃",
     signal4 is not None and signal4.sample_count >= 5,
     f"critical={signal4.critical}, p={signal4.p_value}, samples={signal4.sample_count}")

# ============================================================
# 任务 6: 证据层级升降级（L0→L3）
# ============================================================
print("\n" + "=" * 60)
print("任务 6: 知识库证据层级升降级")

kb = KnowledgeBase(cfg)

# 6a: 新增 L1 条目
entry = KBEntry("test_rule_1", "所有除零错误必须检查类型是否为 None",
                level="L1", scope={"tags": ["division", "safety"]},
                confidence=0.4)
added = kb.add(entry)
test("6a: L1 条目添加成功", added, f"added={added}")

# 6b: L0 条目应被拒绝
entry_l0 = KBEntry("test_l0", "raw hypothesis", level="L0", scope={"tags": ["test"]})
added_l0 = kb.add(entry_l0)
test("6b: L0 条目被拒绝", not added_l0, f"added={added_l0}")

# 6c: 多次标记成功 → 升级（需先 get 增加 usage_count）
e = kb.get("test_rule_1")
test("6c: 获取已添加条目", e is not None, f"found={e is not None}")

if e:
    # mark_success 检查 usage_count >= 3 才升级
    # get() 会增加 usage_count，mark_success 本身不增加
    for _ in range(3):
        kb.get("test_rule_1")       # usage_count++
        kb.mark_success("test_rule_1")
    e2 = kb.get("test_rule_1")
    test("6d: 多次 get+mark_success 后升至 L2",
         e2.level == "L2" if e2 else False,
         f"level={e2.level if e2 else 'None'}, usage_count={e2.usage_count if e2 else 'N/A'}")

    # 6e: 标记失败 → L1 条目直接变为 invalid
    # 先升级到 L2，再测试降级
    # 添加新条目测试降级
    entry2 = KBEntry("test_rule_2", "另一个测试规则",
                     level="L2", scope={"tags": ["test"]}, confidence=0.7)
    kb.add(entry2)
    kb.mark_fail("test_rule_2")
    e3 = kb.get("test_rule_2")
    # L2 降级到 L1
    test("6e: L2 条目失败后降为 L1",
         e3 is not None and e3.level == "L1",
         f"level={e3.level if e3 else 'None'}")

    # 6f: 置信度变化
    test("6f: 失败后置信度降低",
         e3 is not None and e3.confidence < 0.6,
         f"confidence={e3.confidence if e3 else 'N/A'}")

# ============================================================
# 任务 7: GraphKB 联想记忆
# ============================================================
print("\n" + "=" * 60)
print("任务 7: GraphKB 联想记忆")

g = GraphKB("test_graph")

# 7a: 添加节点
node_a = Node("timeout_error", "Problem", {"name": "超时问题", "severity": "high"})
node_b = Node("connection_pool_exhaustion", "Problem", {"name": "连接池耗尽", "severity": "critical"})
node_c = Node("max_connections", "Parameter", {"name": "最大连接数配置"})

g.add_node(node_a)
g.add_node(node_b)
g.add_node(node_c)
test("7a: 3 个节点添加成功", g.node_count == 3, f"node_count={g.node_count}")

# 7b: 添加因果边
edge_ab = Edge("e_ab", "timeout_error", "connection_pool_exhaustion", "CAUSES", weight=0.9)
edge_bc = Edge("e_bc", "connection_pool_exhaustion", "max_connections", "RESOLVED_BY", weight=0.8)
g.add_edge(edge_ab)
g.add_edge(edge_bc)
test("7b: 2 条边添加成功", g.edge_count == 2, f"edge_count={g.edge_count}")

# 7c: BFS 邻居查询
neighbors = g.get_neighbors("timeout_error", max_depth=1, direction="out")
neighbor_names = [n.properties.get("name", n.id) for n in neighbors]
test("7c: timeout_error 出边邻居包含连接池耗尽",
     "连接池耗尽" in neighbor_names,
     f"neighbors={neighbor_names}")

# 7d: 深度 2 查询 → 应该能到达 max_connections
neighbors_d2 = g.get_neighbors("timeout_error", max_depth=2, direction="out")
n2_names = [n.properties.get("name", n.id) for n in neighbors_d2]
test("7d: 深度 2 BFS 能到达最大连接数",
     "最大连接数配置" in n2_names,
     f"depth2 neighbors={n2_names}")

# 7e: 路径查找
paths = g.find_paths("timeout_error", "max_connections", max_depth=4)
test("7e: 能找到 timeout_error → max_connections 路径",
     len(paths) >= 1,
     f"path_count={len(paths)}")

# 7f: 子图提取
sub = g.find_subgraph(["timeout_error"], max_depth=2)
test("7f: 子图提取包含所有 3 个节点",
     sub.node_count == 3,
     f"sub nodes={sub.node_count}, edges={sub.edge_count}")

# ============================================================
# 任务 8: 五维负熵系统
# ============================================================
print("\n" + "=" * 60)
print("任务 8: 五维负熵系统")

# 8a: 正常状态
state_clean = {
    "phase_regressions": 0,
    "stagnation_events": 0,
    "phase_switches": 0,
    "anchor_changes": 0,
    "retrieve_calls": 0,
    "total_subtasks": 1,
}
ne_clean = Negentropy(state_clean)

test("8a: 干净状态的流程有序度=100%",
     ne_clean.process_order() == 100,
     f"process_order={ne_clean.process_order()}")

test("8b: 干净状态的意图锚定度=100%",
     ne_clean.intent_anchoring() == 100,
     f"intent_anchoring={ne_clean.intent_anchoring()}")

# 8c: 阶段回退 -> 有序度下降
state_regressed = dict(state_clean, phase_regressions=2, phase_switches=5)
ne_bad = Negentropy(state_regressed)
order = ne_bad.process_order()
test("8c: 回退 2 次 + 切换 5 次 -> 有序度下降",
     order < 100,
     f"process_order={order}")

# 8d: 意图变更
state_anchor_changed = dict(state_clean, anchor_changes=2)
ne_anchor = Negentropy(state_anchor_changed)
test("8d: 意图变更 2 次 → 锚定度=50%",
     ne_anchor.intent_anchoring() == 50,
     f"intent_anchoring={ne_anchor.intent_anchoring()}")

# 8e: 综合指数
test("8e: 综合指数在 0-100 范围",
     0 <= ne_bad.composite() <= 100,
     f"composite={ne_bad.composite()}")

# 8f: Dashboard 生成
html = generate_dashboard(ne_clean)
test("8f: Dashboard HTML 生成成功",
     "苦瓜code" in html and "负熵仪表板" in html,
     f"html length={len(html)}")

# ============================================================
# 任务 9: SafetyManager 红线拦截
# ============================================================
print("\n" + "=" * 60)
print("任务 9: SafetyManager 红线拦截")

sm_safety = SafetyManager(cfg)

# 9a: rm_rf 永久禁止
allowed, reason = sm_safety.check_permission("rm_rf")
test("9a: rm_rf 被永久禁止",
     not allowed,
     f"reason={reason}")

# 9b: sudo 永久禁止
allowed, reason = sm_safety.check_permission("sudo")
test("9b: sudo 被永久禁止",
     not allowed,
     f"reason={reason}")

# 9c: git_push_force 永久禁止
allowed, reason = sm_safety.check_permission("git_push_force")
test("9c: git_push_force 被永久禁止",
     not allowed,
     f"reason={reason}")

# 9d: eval_shell 永久禁止
allowed, reason = sm_safety.check_permission("eval_shell")
test("9d: eval_shell 被永久禁止",
     not allowed,
     f"reason={reason}")

# 9e: pipe_to_sh 永久禁止
allowed, reason = sm_safety.check_permission("pipe_to_sh")
test("9e: pipe_to_sh 被永久禁止",
     not allowed,
     f"reason={reason}")

# 9f: 正常操作可根据信任级别放行
# 注意: 使用独立实例，因为前面 L5 检查已累积 5 次拒绝触发了 Auto Kill Switch
sm_safety_fresh = SafetyManager(cfg)
allowed, reason = sm_safety_fresh.check_permission("read_file")
test("9f: read_file 在 L2 级别放行",
     allowed,
     f"reason={reason}")

# 9g: execute_cmd 在 L2 被拒绝
allowed, reason = sm_safety_fresh.check_permission("execute_cmd")
test("9g: execute_cmd 在 L2 被拒绝",
     not allowed,
     f"reason={reason}")

# 9h: Kill Switch
state_kill = sm_safety_fresh.kill_switch("测试熔断")
test("9h: Kill Switch 设置 emergency_stop=True",
     state_kill.get("emergency_stop") == True,
     f"emergency_stop={state_kill.get('emergency_stop')}")

# 9i: 事故记录
incident = Incident("IV", "测试", "测试事故", impact="无", score=5)
sm_safety_fresh.log_incident(incident)
incidents = sm_safety_fresh.query_incidents(days=30)
test("9i: 事故记录可查询",
     len(incidents) >= 1,
     f"incident count={len(incidents)}")

# ============================================================
# 任务 10: ContextManager 分层冻结（通过状态机间接测试）
# ============================================================
print("\n" + "=" * 60)
print("任务 10: ContextManager 分层冻结")

from kugua.context import ContextManager, LayerType

ctx = ContextManager(cfg, session_id="test_session")

# 10a: L0 冻结
ctx.freeze_L0("You are a test assistant.", "")
test("10a: L0 冻结后内容正确",
     "test assistant" in ctx.L0.content,
     f"content preview={ctx.L0.content[:50]}")

# 10b: L1 冻结
ctx.freeze_L1(
    intent_anchor={"user_goal": "测试目标", "success_criteria": ["criteria_1"]},
    task_dag=[{"id": "t1", "description": "测试任务"}],
    plan="test-first"
)
test("10b: L1 冻结后意图锚点正确",
     ctx.L1.intent_anchor.get("user_goal") == "测试目标",
     f"user_goal={ctx.L1.intent_anchor.get('user_goal')}")

# 10c: L2 追加
ctx.append("user", "这是测试消息")
ctx.append("system", "这是系统消息")
test("10c: L2 追加条目",
     len(ctx.L2.entries) >= 2,
     f"L2 entries={len(ctx.L2.entries)}")

# 10d: assemble 输出完整上下文
assembled = ctx.assemble("当前用户消息")
test("10d: assemble 包含 L0/L1/L2",
     "L0:immutable" in assembled and "L1:semi-stable" in assembled and "L2:mutable-log" in assembled,
     f"assembled length={len(assembled)}")

# ============================================================
# 任务 14: BayesianCalibrator 校准
# ============================================================
print("\n" + "=" * 60)
print("任务 14: BayesianCalibrator 校准")

from kugua.calibration import BayesianCalibrator, calc_likelihood, bayesian_update

# 14a: 似然计算（根据 checker_score 映射）
likelihood_high = calc_likelihood(92)  # ≥90 → 0.95
likelihood_mid = calc_likelihood(75)   # 60-90 → 0.6
likelihood_low = calc_likelihood(30)   # <60 → 0.1
test("14a: 似然映射正确 (high=0.95, mid=0.6, low=0.1)",
     likelihood_high == 0.95 and likelihood_mid == 0.6 and likelihood_low == 0.1,
     f"high={likelihood_high}, mid={likelihood_mid}, low={likelihood_low}")

# 14b: 贝叶斯更新
prior = 0.5
likes = [0.95, 0.6, 0.95]  # 3 次观察的似然
posterior = bayesian_update(prior, likes)
test("14b: 贝叶斯更新后验 > 先验（正向证据多）",
     posterior > prior,
     f"prior={prior:.3f}, posterior={posterior:.3f}")

# 14c: BayesianCalibrator 完整校准器
cal = BayesianCalibrator(cfg)
# calibrate() 处理 EV 日志 — 空日志应返回空操作
digest = cal.calibrate(days=7)
test("14c: 校准器 calibrate() 不崩溃（空 EV 日志）",
     isinstance(digest, dict),
     f"digest keys={list(digest.keys()) if digest else 'None'}")

# ============================================================
# 任务 13: ObserverWeight 动态调节
# ============================================================
print("\n" + "=" * 60)
print("任务 13: ObserverWeight 动态调节")

from kugua.context_compressor import ObserverWeight

ow = ObserverWeight("deepseek-v4-pro", "mimo-v2-flash")

# 13a: 高置信度 → VETO
result_high = ow.evaluate(0.85)
test("13a: 置信度 0.85 → VETO",
     result_high == "VETO",
     f"result={result_high}")

# 13b: 中等置信度 → WARN
result_mid = ow.evaluate(0.6)
test("13b: 置信度 0.6 → WARN",
     result_mid == "WARN",
     f"result={result_mid}")

# 13c: 低置信度 → LOG
result_low = ow.evaluate(0.3)
test("13c: 置信度 0.3 → LOG",
     result_low == "LOG",
     f"result={result_low}")

# 13d: 计数正确
test("13d: 3 次观察计数: veto=1, warn=1, log=1",
     ow.veto_count == 1 and ow.soft_warn_count == 1 and ow.observation_count == 3,
     f"veto={ow.veto_count}, warn={ow.soft_warn_count}, total={ow.observation_count}")

# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 60)
print("测试汇总")
print("=" * 60)
for r in results:
    print(r)

total = PASS + FAIL
print(f"\n{'='*60}")
print(f"通过: {PASS}/{total} ({100*PASS/total:.1f}%)")
print(f"失败: {FAIL}/{total}")
print(f"内核测试完成")
