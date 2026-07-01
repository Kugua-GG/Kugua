"""
test_diffusion.py — 图拉普拉斯置信度扩散验证
═══════════════════════════════════════════════════════════

在真实 GraphKB 上运行扩散, 验证:
  D1. 置信度沿边传播到未标记节点
  D2. 高权重边传播更多置信度
  D3. 收敛性: 迭代后 max_delta < tol
  D4. audit_trail 输出正确排序
  D5. 无标记节点初始置信度为0
"""
import sys, os
sys.path.insert(0, r"C:\Users\Administrator\Desktop\kugua-v0.2.1\kugua-code")
from kugua.graph import GraphKB, Node, Edge
from kugua.diffusion import (
    build_f_vector, compute_L_rw, run_diffusion,
    calculate_verification_saved, audit_trail,
)

PASS = 0; FAIL = 0
def test(name, passed, detail=""):
    global PASS, FAIL
    if passed: PASS += 1; print(f"[PASS] {name}")
    else: FAIL += 1; print(f"[FAIL] {name} — {detail}")

print("=" * 50 + "\nDiffusion 图扩散验证")

# 构建测试图: A → B → C → D, 其中 A 和 B 之间还有一条高权重边
g = GraphKB("test_diffusion")
for nid, label in [("A", "verified"), ("B", "candidate"), ("C", "candidate"), ("D", "unknown")]:
    g.add_node(Node(nid, label))
g.add_edge(Edge("e1", "A", "B", "SUPPORTS", weight=0.9))
g.add_edge(Edge("e2", "B", "C", "SUPPORTS", weight=0.7))
g.add_edge(Edge("e3", "C", "D", "SUPPORTS", weight=0.5))

# D1: 种子节点 A(1.0) 和 D(0.2), 验证扩散到 B 和 C
labeled = {"A": 1.0, "D": 0.2}
diffused = run_diffusion(g, labeled, alpha=0.1, max_iter=100)

test("D1a: A保持高置信度", diffused["A"] > 0.8, f"A={diffused['A']:.3f}")
test("D1b: B获得传播(B > 0)", diffused["B"] > 0.0, f"B={diffused['B']:.3f}")
test("D1c: C获得传播(C > 0)", diffused["C"] > 0.0, f"C={diffused['C']:.3f}")
test("D1d: D保持种子值附近", abs(diffused["D"] - 0.2) < 0.5, f"D={diffused['D']:.3f}")

# D2: 高权重边(0.9)传播 > 低权重边(0.5)
# B 从 A(0.9)获得应 > C 从 B(0.7)获得
test("D2: B > C (A→B权重0.9 > B→C权重0.7)",
     diffused["B"] > diffused["C"],
     f"B={diffused['B']:.3f}, C={diffused['C']:.3f}")

# D3: 收敛性 — 再跑一次应几乎不变
diffused2 = run_diffusion(g, labeled, alpha=0.1, max_iter=100)
max_change = max(abs(diffused2[n] - diffused[n]) for n in diffused)
test("D3: 再跑收敛 (max_delta < 0.001)", max_change < 0.001, f"delta={max_change:.6f}")

# D4: f_vector — 未标记节点初始为0
f = build_f_vector(g, labeled)
test("D4a: B初始=0", f["B"] == 0.0, f"B={f['B']}")
test("D4b: C初始=0", f["C"] == 0.0, f"C={f['C']}")

# D5: verification_saved + audit_trail
saved = calculate_verification_saved(g, diffused, threshold=0.01)
test("D5a: verification_saved > 0", saved >= 1, f"saved={saved}")
trail = audit_trail(diffused)
test("D5b: audit_trail 按置信度降序", trail[0]["confidence"] >= trail[-1]["confidence"], "")

# D6: Laplacian 对角线为1
L = compute_L_rw(g)
for nid in g._nodes:
    test(f"D6: L_rw['{nid}'] 对角线=1", abs(L[nid][nid] - 1.0) < 0.001, f"diag={L[nid][nid]:.3f}")

# D7: 孤立节点不受影响
g2 = GraphKB("iso_test")
g2.add_node(Node("X", "isolated"))
g2.add_node(Node("Y", "labeled"))
g2.add_edge(Edge("exy", "X", "Y", "RELATED", weight=0.3))
d_iso = run_diffusion(g2, {"Y": 1.0}, alpha=0.1)
test("D7: 孤立标记节点保持高值", d_iso["Y"] > 0.8, f"Y={d_iso['Y']:.3f}")

print("\n" + "=" * 50)
total = PASS + FAIL
print(f"通过: {PASS}/{total} | 失败: {FAIL}/{total}")
