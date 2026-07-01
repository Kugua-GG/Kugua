"""
示例 3: 用 kugua 的置信度 API 做幻觉检测

在 RAG pipeline 中，每次 LLM 生成后调用 Guardian 检查置信度。
低于阈值的回答自动标记为"需人工复核"。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kugua-code"))

from kugua.client import GuardianClient

gc = GuardianClient(confidence_threshold=0.7)

def rag_generate(question: str) -> dict:
    """模拟 RAG pipeline: 检索 → 生成 → kugua 审查。"""
    # 模拟 LLM 生成
    answers = {
        "vaccine": ("Vaccines do not cause autism. This has been verified by "
                     "studies involving millions of children.", 0.95),
        "flat earth": ("The Earth is actually flat and NASA has been hiding "
                       "this from the public for decades.", 0.35),
        "climate": ("Climate change is primarily driven by anthropogenic CO2 "
                    "emissions, supported by >97% of climate scientists.", 0.88),
    }

    answer, confidence = answers.get(
        question, ("I don't have enough information to answer this question.", 0.5))

    # kugua 审查
    decision = gc.check(
        prompt=answer,
        action="write_file",
        confidence=confidence,
        session_id=f"rag-{question}",
    )

    return {
        "question": question,
        "answer": answer,
        "confidence": confidence,
        "needs_review": not decision.allowed,
        "guardian_action": decision.action,
        "suggestion": decision.suggestion,
    }


print("RAG + kugua Hallucination Detector")
print("=" * 50)

for q in ["vaccine", "flat earth", "climate"]:
    result = rag_generate(q)
    flag = "⚠️ NEEDS REVIEW" if result["needs_review"] else "✅ VERIFIED"
    print(f"  [{flag}] Q: {q}")
    print(f"    Confidence: {result['confidence']:.2f}")
    if result["needs_review"]:
        print(f"    Action: {result['guardian_action']}")
