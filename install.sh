#!/bin/bash
# kugua core v0.2.1 — 一键安装 (Unix/macOS)
set -e

echo "========================================"
echo "  kugua core v0.2.1 — 一键安装"
echo "========================================"
echo ""

cd "$(dirname "$0")"

# ── [1/5] 语言选择 ──────────────────────────
echo "[1/5] 语言选择 / Language Selection"
echo ""
echo "   [1] 简体中文 (zh-CN)"
echo "   [2] 繁體中文 (zh-TW)"
echo "   [3] English (en)"
echo "   [4] 日本語 (ja)"
echo ""

read -p "  请选择 / Select (1-4) [默认/default 1]: " LANG_CHOICE
LANG_CHOICE=${LANG_CHOICE:-1}

case "$LANG_CHOICE" in
  1) KUGUA_LANG="zh-CN" ;;
  2) KUGUA_LANG="zh-TW" ;;
  3) KUGUA_LANG="en" ;;
  4) KUGUA_LANG="ja" ;;
  *) KUGUA_LANG="zh-CN" ;;
esac

export KUGUA_LANG
echo "  语言已设置为 / Language set to: $KUGUA_LANG"
echo ""

# 持久化 (bashrc/zshrc)
if [ -f "$HOME/.bashrc" ]; then
  grep -q "KUGUA_LANG" "$HOME/.bashrc" 2>/dev/null || echo "export KUGUA_LANG=$KUGUA_LANG" >> "$HOME/.bashrc"
elif [ -f "$HOME/.zshrc" ]; then
  grep -q "KUGUA_LANG" "$HOME/.zshrc" 2>/dev/null || echo "export KUGUA_LANG=$KUGUA_LANG" >> "$HOME/.zshrc"
fi

# ── [2/5] pip install ──────────────────────
echo "[2/5] pip install -e ."
pip install -e . --quiet

# ── [3/5] 验证安装 ─────────────────────────
echo ""
echo "[3/5] 验证安装"
python -c "from kugua import KuguaConfig, get_dashboard_summary; from kugua.i18n import t, SUPPORTED_LANGS; print('  导入成功 · 已加载', len(SUPPORTED_LANGS), '种语言')"

# ── [4/5] 配置 MCP ─────────────────────────
echo ""
echo "[4/5] MCP 配置"
echo "  .mcp.json 中应包含 kugua 条目:"
echo '  {'
echo '    "kugua": {'
echo '      "command": "python",'
echo "      \"args\": [\"$(pwd)/mcp/server.py\"],"
echo '      "env": {'
echo "        \"KUGUA_LANG\": \"$KUGUA_LANG\","
echo '        "KUGUA_ARTIFACTS_DIR": "~/.claude/.codex/artifacts",'
echo "        \"KUGUA_CODE_DIR\": \"$(pwd)/kugua\""
echo '      }'
echo '    }'
echo '  }'

# ── [5/5] 配置 LLM ─────────────────────────
echo ""
echo "[5/5] 配置 LLM 模型"
echo ""
echo "  kugua core 需要配置 LLM 模型才能运行。"
echo "  至少需要 1 个，强烈建议提供 2 个（主力和观察者各一）。"
echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │ 主力模型 (Worker/Checker) — 处理复杂任务     │"
echo "  │ 观察者 (FreshObserver)   — 检测幻觉，需轻量  │"
echo "  │                              (建议不同模型)  │"
echo "  └─────────────────────────────────────────────┘"
echo ""

read -p "  是否现在运行配置向导？(Y/n): " RUN_SETUP
RUN_SETUP=${RUN_SETUP:-y}

if [ "$RUN_SETUP" != "n" ] && [ "$RUN_SETUP" != "N" ]; then
    python -m kugua.setup_wizard
fi

echo ""
echo "========================================"
echo "  安装完成！"
echo ""
echo "  支持的语言: 简体中文 | 繁體中文 | English | 日本語"
echo "  切换语言: export KUGUA_LANG=en  (当前: $KUGUA_LANG)"
echo ""
echo "  7 个 MCP 工具:"
echo "    status_all · kb_query · kb_snapshot"
echo "    double_loop_check · observer_gate"
echo "    negentropy_dash · csd_status"
echo ""
echo "  快速测试:"
echo "    python -c \"from kugua import get_dashboard_summary; print(get_dashboard_summary())\""
echo "========================================"
