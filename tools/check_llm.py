"""Quick LLM connectivity check — run with: MIMO_API_KEY=sk-... python tools/check_llm.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kugua.executor import LLMClient

key = os.getenv("MIMO_API_KEY", "")
if not key:
    print("MIMO_API_KEY not set")
    sys.exit(1)

c = LLMClient()
r = c.chat(messages=[{"role": "user", "content": "say pong"}], max_tokens=16, temperature=0.0)
print(f"ok={r.get('ok')} model={r.get('model','?')} content={r.get('content','')[:40]}")
print(f"error={r.get('error','')[:100]}")
