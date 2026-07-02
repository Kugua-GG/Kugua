"""
kugua core · i18n 多语言模块 v0.2.1

支持四语言:
  zh-CN — 简体中文 (默认)
  zh-TW — 繁体中文
  en    — 英语
  ja    — 日语

使用方式:
  from kugua.i18n import t, set_lang, get_lang, SUPPORTED_LANGS
  set_lang("en")
  print(t("status_all.title"))  # → "kugua core v0.2.1 · Core Metrics"

环境变量:
  KUGUA_LANG=ja  → 日语
"""

import os
from typing import Dict

# ── 支持的语言列表 ──────────────────────────────────
SUPPORTED_LANGS: Dict[str, str] = {
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
    "en":    "English",
    "ja":    "日本語",
}

# ── 当前语言 (优先级: KUGUA_LANG > 默认 zh-CN) ──────
_current_lang = os.getenv("KUGUA_LANG", "zh-CN")
if _current_lang not in SUPPORTED_LANGS:
    _current_lang = "zh-CN"


def get_lang() -> str:
    """返回当前语言代码。"""
    return _current_lang


def set_lang(lang: str) -> None:
    """切换语言。无效代码回退 zh-CN。"""
    global _current_lang
    if lang in SUPPORTED_LANGS:
        _current_lang = lang
    else:
        _current_lang = "zh-CN"


def t(key: str, **kwargs) -> str:
    """取翻译。key 不存在时返回 key 本身（方便调试）。支持 {var} 模板替换。"""
    text = _STRINGS.get(key, {}).get(_current_lang)
    if text is None:
        # 回退链: zh-CN → key itself
        text = _STRINGS.get(key, {}).get("zh-CN", key)
    if kwargs:
        for k, v in kwargs.items():
            text = text.replace("{" + k + "}", str(v))
    return text


def list_langs() -> str:
    """列出所有支持的语言。"""
    lines = [f"  {code} — {name}" for code, name in SUPPORTED_LANGS.items()]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 翻译字符串表 (key → {zh-CN, zh-TW, en, ja})
# ═══════════════════════════════════════════════════════════

_STRINGS: Dict[str, Dict[str, str]] = {

    # ── 通用 ─────────────────────────────────────────
    "common.title": {
        "zh-CN": "🍈 苦瓜code",
        "zh-TW": "🍈 苦瓜code",
        "en":    "🍈 kugua core",
        "ja":    "🍈 苦瓜code",
    },
    "common.version": {
        "zh-CN": "v0.2.1",
        "zh-TW": "v0.2.1",
        "en":    "v0.2.1",
        "ja":    "v0.2.1",
    },
    "common.separator": {
        "zh-CN": "──────────────────────────────",
        "zh-TW": "──────────────────────────────",
        "en":    "──────────────────────────────",
        "ja":    "──────────────────────────────",
    },
    "common.unit_entry": {
        "zh-CN": "条",
        "zh-TW": "條",
        "en":    "entries",
        "ja":    "件",
    },

    # ── 等级标签 ─────────────────────────────────────
    "tier.excellent": {
        "zh-CN": "优秀",
        "zh-TW": "優秀",
        "en":    "Excellent",
        "ja":    "優秀",
    },
    "tier.good": {
        "zh-CN": "尚可",
        "zh-TW": "尚可",
        "en":    "Good",
        "ja":    "良好",
    },
    "tier.needs_improvement": {
        "zh-CN": "需改进",
        "zh-TW": "需改進",
        "en":    "Needs Improvement",
        "ja":    "要改善",
    },
    "tier.critical": {
        "zh-CN": "严重熵增",
        "zh-TW": "嚴重熵增",
        "en":    "Critical Entropy",
        "ja":    "深刻なエントロピー",
    },

    # ── status_all ───────────────────────────────────
    "status_all.title": {
        "zh-CN": "kugua core v0.2.1 · 六项核心指标",
        "zh-TW": "kugua core v0.2.1 · 六項核心指標",
        "en":    "kugua core v0.2.1 · Core Metrics",
        "ja":    "kugua core v0.2.1 · 六指標ダッシュボード",
    },
    "status_all.col_metric": {
        "zh-CN": "指标",
        "zh-TW": "指標",
        "en":    "Metric",
        "ja":    "指標",
    },
    "status_all.col_value": {
        "zh-CN": "数值",
        "zh-TW": "數值",
        "en":    "Value",
        "ja":    "値",
    },
    "status_all.col_note": {
        "zh-CN": "备注",
        "zh-TW": "備註",
        "en":    "Note",
        "ja":    "備考",
    },
    "status_all.composite": {
        "zh-CN": "1.综合负熵",
        "zh-TW": "1.綜合負熵",
        "en":    "1.Composite",
        "ja":    "1.総合ネゲントロピー",
    },
    "status_all.process_order": {
        "zh-CN": "2.流程有序",
        "zh-TW": "2.流程有序",
        "en":    "2.Process Order",
        "ja":    "2.プロセス秩序",
    },
    "status_all.intent_anchoring": {
        "zh-CN": "3.意图锚定",
        "zh-TW": "3.意圖錨定",
        "en":    "3.Intent Anchor",
        "ja":    "3.意図アンカー",
    },
    "status_all.knowledge_efficacy": {
        "zh-CN": "4.知识生效",
        "zh-TW": "4.知識生效",
        "en":    "4.Knowledge Eff.",
        "ja":    "4.知識有効性",
    },
    "status_all.double_loop_efficacy": {
        "zh-CN": "5.双环效能",
        "zh-TW": "5.雙環效能",
        "en":    "5.Double-Loop Eff.",
        "ja":    "5.ダブルループ効能",
    },
    "status_all.csd_critical": {
        "zh-CN": "6.CSD临界",
        "zh-TW": "6.CSD臨界",
        "en":    "6.CSD Critical",
        "ja":    "6.CSDクリティカル",
    },
    "status_all.kb_active": {
        "zh-CN": "KB活跃: {n}",
        "zh-TW": "KB活躍: {n}",
        "en":    "KB active: {n}",
        "ja":    "KB有効: {n}",
    },
    "status_all.double_loop_note": {
        "zh-CN": "永久熵减贡献",
        "zh-TW": "永久熵減貢獻",
        "en":    "permanent entropy reduction",
        "ja":    "永続的エントロピー削減",
    },
    "status_all.csd_note": {
        "zh-CN": "信号数",
        "zh-TW": "信號數",
        "en":    "signals",
        "ja":    "シグナル数",
    },

    # ── negentropy_dash ──────────────────────────────
    "negentropy_dash.title": {
        "zh-CN": "负熵仪表板",
        "zh-TW": "負熵儀表板",
        "en":    "Negentropy Dashboard",
        "ja":    "ネゲントロピーダッシュボード",
    },
    "negentropy_dash.composite": {
        "zh-CN": "综合指数",
        "zh-TW": "綜合指數",
        "en":    "Composite Index",
        "ja":    "総合指数",
    },
    "negentropy_dash.five_dimensions": {
        "zh-CN": "五维度",
        "zh-TW": "五維度",
        "en":    "Five Dimensions",
        "ja":    "五次元",
    },
    "negentropy_dash.process_order": {
        "zh-CN": "流程有序度",
        "zh-TW": "流程有序度",
        "en":    "Process Order",
        "ja":    "プロセス秩序度",
    },
    "negentropy_dash.intent_anchoring": {
        "zh-CN": "意图锚定度",
        "zh-TW": "意圖錨定度",
        "en":    "Intent Anchoring",
        "ja":    "意図アンカー度",
    },
    "negentropy_dash.knowledge_efficacy": {
        "zh-CN": "知识生效度",
        "zh-TW": "知識生效度",
        "en":    "Knowledge Efficacy",
        "ja":    "知識有効度",
    },
    "negentropy_dash.information_fidelity": {
        "zh-CN": "信息保真度",
        "zh-TW": "信息保真度",
        "en":    "Information Fidelity",
        "ja":    "情報忠実度",
    },
    "negentropy_dash.double_loop_efficacy": {
        "zh-CN": "双环效能",
        "zh-TW": "雙環效能",
        "en":    "Double-Loop Efficacy",
        "ja":    "ダブルループ効能",
    },
    "negentropy_dash.permanent_entropy": {
        "zh-CN": "永久熵减贡献",
        "zh-TW": "永久熵減貢獻",
        "en":    "permanent entropy reduction",
        "ja":    "永続的エントロピー削減",
    },
    "negentropy_dash.raw_data": {
        "zh-CN": "原始数据",
        "zh-TW": "原始數據",
        "en":    "Raw Data",
        "ja":    "生データ",
    },
    "negentropy_dash.double_loop_events": {
        "zh-CN": "双环事件",
        "zh-TW": "雙環事件",
        "en":    "Double-Loop Events",
        "ja":    "ダブルループイベント",
    },

    # ── csd_status ───────────────────────────────────
    "csd_status.title": {
        "zh-CN": "临界慢化检测器状态",
        "zh-TW": "臨界慢化檢測器狀態",
        "en":    "Critical Slowing Detector Status",
        "ja":    "臨界減速検出器ステータス",
    },
    "csd_status.no_data": {
        "zh-CN": "临界慢化检测器无数据。系统尚未积累足够的错误模式。",
        "zh-TW": "臨界慢化檢測器無數據。系統尚未積累足夠的錯誤模式。",
        "en":    "CSD has no data. The system has not yet accumulated sufficient error patterns.",
        "ja":    "CSDにデータがありません。システムはまだ十分なエラーパターンを蓄積していません。",
    },
    "csd_status.critical_label": {
        "zh-CN": "临界",
        "zh-TW": "臨界",
        "en":    "CRITICAL",
        "ja":    "クリティカル",
    },
    "csd_status.normal_label": {
        "zh-CN": "正常",
        "zh-TW": "正常",
        "en":    "normal",
        "ja":    "正常",
    },
    "csd_status.summary": {
        "zh-CN": "总计: {total} 个追踪对, {critical} 个临界",
        "zh-TW": "總計: {total} 個追蹤對, {critical} 個臨界",
        "en":    "Total: {total} tracked pairs, {critical} critical",
        "ja":    "合計: {total} 追跡ペア, {critical} クリティカル",
    },

    # ── kb_query ─────────────────────────────────────
    "kb.empty": {
        "zh-CN": "知识库为空。系统将在运行过程中自动积累知识。",
        "zh-TW": "知識庫為空。系統將在運行過程中自動積累知識。",
        "en":    "Knowledge base is empty. Knowledge will be accumulated automatically during operation.",
        "ja":    "知識ベースが空です。システムは実行中に自動的に知識を蓄積します。",
    },
    "kb.no_results": {
        "zh-CN": "未找到匹配 '{query}' 的知识条目 (min_level={level})。",
        "zh-TW": "未找到匹配 '{query}' 的知識條目 (min_level={level})。",
        "en":    "No entries matching '{query}' found (min_level={level}).",
        "ja":    "「{query}」に一致するエントリが見つかりません (min_level={level})。",
    },
    "kb.result_footer": {
        "zh-CN": "共 {count} 条 (活跃: {active})",
        "zh-TW": "共 {count} 條 (活躍: {active})",
        "en":    "Total: {count} entries (active: {active})",
        "ja":    "合計: {count} 件 (有効: {active})",
    },

    # ── kb_snapshot ──────────────────────────────────
    "kb_snapshot.title": {
        "zh-CN": "知识库快照",
        "zh-TW": "知識庫快照",
        "en":    "Knowledge Base Snapshot",
        "ja":    "知識ベーススナップショット",
    },
    "kb_snapshot.level_dist": {
        "zh-CN": "证据层级分布",
        "zh-TW": "證據層級分佈",
        "en":    "Evidence Level Distribution",
        "ja":    "証拠レベル分布",
    },
    "kb_snapshot.l3_desc": {
        "zh-CN": "L3 (10x验证+反例)",
        "zh-TW": "L3 (10x驗證+反例)",
        "en":    "L3 (10x verified + counter-example tested)",
        "ja":    "L3 (10x検証+反例テスト)",
    },
    "kb_snapshot.l2_desc": {
        "zh-CN": "L2 (3x验证)",
        "zh-TW": "L2 (3x驗證)",
        "en":    "L2 (3x verified)",
        "ja":    "L2 (3x検証)",
    },
    "kb_snapshot.l1_desc": {
        "zh-CN": "L1 (单次验证)",
        "zh-TW": "L1 (單次驗證)",
        "en":    "L1 (single verification)",
        "ja":    "L1 (単一検証)",
    },
    "kb_snapshot.active_total": {
        "zh-CN": "活跃总计",
        "zh-TW": "活躍總計",
        "en":    "Active Total",
        "ja":    "有効合計",
    },
    "kb_snapshot.status_dist": {
        "zh-CN": "状态分布",
        "zh-TW": "狀態分佈",
        "en":    "Status Distribution",
        "ja":    "ステータス分布",
    },
    "kb_snapshot.efficacy_title": {
        "zh-CN": "双环效能",
        "zh-TW": "雙環效能",
        "en":    "Double-Loop Efficacy",
        "ja":    "ダブルループ効能",
    },
    "kb_snapshot.verified_events": {
        "zh-CN": "已验证熵减事件",
        "zh-TW": "已验证熵減事件",
        "en":    "Verified Entropy-Reducing Events",
        "ja":    "検証済みエントロピー削減イベント",
    },
    "kb_snapshot.total_delta_s": {
        "zh-CN": "累计 ΔS",
        "zh-TW": "累計 ΔS",
        "en":    "Cumulative ΔS",
        "ja":    "累積 ΔS",
    },
    "kb_snapshot.pending": {
        "zh-CN": "追踪中",
        "zh-TW": "追蹤中",
        "en":    "Pending",
        "ja":    "保留中",
    },
    "kb_snapshot.reverted": {
        "zh-CN": "已回退",
        "zh-TW": "已回退",
        "en":    "Reverted",
        "ja":    " revert済み",
    },

    # ── double_loop_check ────────────────────────────
    "dlc.triggered_title": {
        "zh-CN": "双环触发！",
        "zh-TW": "雙環觸發！",
        "en":    "Double-Loop Triggered!",
        "ja":    "ダブルループ発動！",
    },
    "dlc.error_type": {
        "zh-CN": "错误类型",
        "zh-TW": "錯誤類型",
        "en":    "Error Type",
        "ja":    "エラータイプ",
    },
    "dlc.gv_id": {
        "zh-CN": "治理变量",
        "zh-TW": "治理變量",
        "en":    "Governance Variable",
        "ja":    "ガバナンス変数",
    },
    "dlc.sample_count": {
        "zh-CN": "样本数",
        "zh-TW": "樣本數",
        "en":    "Sample Count",
        "ja":    "サンプル数",
    },
    "dlc.trend_label": {
        "zh-CN": "趋势",
        "zh-TW": "趨勢",
        "en":    "Trend",
        "ja":    "トレンド",
    },
    "dlc.triggered_title_plural": {
        "zh-CN": "个双环触发条件",
        "zh-TW": "個雙環觸發條件",
        "en":    "double-loop trigger conditions",
        "ja":    "件のダブルループ発動条件",
    },
    "dlc.trend_increasing": {
        "zh-CN": "递增(恶化)",
        "zh-TW": "遞增(惡化)",
        "en":    "increasing (worsening)",
        "ja":    "増加（悪化）",
    },
    "dlc.trend_decreasing": {
        "zh-CN": "递减",
        "zh-TW": "遞減",
        "en":    "decreasing",
        "ja":    "減少",
    },
    "dlc.trend_none": {
        "zh-CN": "无",
        "zh-TW": "無",
        "en":    "none",
        "ja":    "なし",
    },
    "dlc.suggestion": {
        "zh-CN": "建议: 执行根因分析 → 生成修改提案 → 盲审 → 验证 → 提交",
        "zh-TW": "建議: 執行根因分析 → 生成修改提案 → 盲審 → 驗證 → 提交",
        "en":    "Suggestion: RCA → Generate Proposal → Audit → Validate → Commit",
        "ja":    "提案: RCA → 修正案生成 → 監査 → 検証 → コミット",
    },
    "dlc.not_triggered": {
        "zh-CN": "未触发",
        "zh-TW": "未觸發",
        "en":    "Not triggered",
        "ja":    "未発動",
    },
    "dlc.all_clear": {
        "zh-CN": "无双环触发条件。所有 (error_type, gv_id) 对均未达临界慢化阈值。",
        "zh-TW": "無雙環觸發條件。所有 (error_type, gv_id) 對均未達臨界慢化閾值。",
        "en":    "No double-loop trigger conditions. All (error_type, gv_id) pairs are below the CSD threshold.",
        "ja":    "ダブルループ発動条件なし。すべての(error_type, gv_id)ペアがCSD閾値を下回っています。",
    },

    # ── observer_gate ────────────────────────────────
    "observer.invalid_gate": {
        "zh-CN": "无效门控类型: {type}。可选: GATE_RCA, GATE_PROPOSAL, GATE_AUDIT",
        "zh-TW": "無效門控類型: {type}。可選: GATE_RCA, GATE_PROPOSAL, GATE_AUDIT",
        "en":    "Invalid gate type: {type}. Options: GATE_RCA, GATE_PROPOSAL, GATE_AUDIT",
        "ja":    "無効なゲートタイプ: {type}。選択肢: GATE_RCA, GATE_PROPOSAL, GATE_AUDIT",
    },
    "observer.passed": {
        "zh-CN": "{type} 通过 — 观察者未发现异常。",
        "zh-TW": "{type} 通過 — 觀察者未發現異常。",
        "en":    "{type} PASSED — Observer found no anomalies.",
        "ja":    "{type} 通過 — オブザーバーは異常を検出しませんでした。",
    },
    "observer.no_error_pattern": {
        "zh-CN": "未提供错误模式",
        "zh-TW": "未提供錯誤模式",
        "en":    "No error pattern provided",
        "ja":    "エラーパターン未提供",
    },
    "observer.no_rule_before": {
        "zh-CN": "未提供修改前规则",
        "zh-TW": "未提供修改前規則",
        "en":    "No pre-modification rule provided",
        "ja":    "修正前ルール未提供",
    },
    "observer.not_passed": {
        "zh-CN": "{type} 未通过",
        "zh-TW": "{type} 未通過",
        "en":    "{type} NOT PASSED",
        "ja":    "{type} 不通過",
    },
    "observer.reason": {
        "zh-CN": "原因",
        "zh-TW": "原因",
        "en":    "Reason",
        "ja":    "理由",
    },
    "observer.blocked_at": {
        "zh-CN": "阻塞于",
        "zh-TW": "阻塞於",
        "en":    "Blocked at",
        "ja":    "ブロック箇所",
    },

    # ── 安装 / 启动 ──────────────────────────────────
    "install.select_lang": {
        "zh-CN": "请选择语言 / Please select language:",
        "zh-TW": "請選擇語言 / Please select language:",
        "en":    "Please select language:",
        "ja":    "言語を選択してください / Please select language:",
    },
    "install.lang_set": {
        "zh-CN": "语言已设置为: {lang}",
        "zh-TW": "語言已設置為: {lang}",
        "en":    "Language set to: {lang}",
        "ja":    "言語が設定されました: {lang}",
    },
    "install.starting": {
        "zh-CN": "启动 Kugua MCP Server v0.2.1 (stdio)",
        "zh-TW": "啟動 Kugua MCP Server v0.2.1 (stdio)",
        "en":    "Starting Kugua MCP Server v0.2.1 (stdio)",
        "ja":    "Kugua MCP Server v0.2.1 を起動中 (stdio)",
    },
    "install.artifacts_dir": {
        "zh-CN": "制品目录",
        "zh-TW": "製品目錄",
        "en":    "Artifacts Directory",
        "ja":    "アーティファクトディレクトリ",
    },
    "install.source_dir": {
        "zh-CN": "苦瓜代码",
        "zh-TW": "苦瓜程式碼",
        "en":    "Source Code",
        "ja":    "ソースコード",
    },
    # ── record_error ──────────────────────────────────
    "record_error.recorded": {
        "zh-CN": "错误已记录",
        "zh-TW": "錯誤已記錄",
        "en":    "Error Recorded",
        "ja":    "エラーを記録しました",
    },
    "record_error.recovery_time": {
        "zh-CN": "恢复时间",
        "zh-TW": "恢復時間",
        "en":    "Recovery Time",
        "ja":    "回復時間",
    },
    "record_error.description": {
        "zh-CN": "描述",
        "zh-TW": "描述",
        "en":    "Description",
        "ja":    "説明",
    },
    "record_error.current_state": {
        "zh-CN": "当前状态",
        "zh-TW": "當前狀態",
        "en":    "Current State",
        "ja":    "現在の状態",
    },
    "record_error.enough_samples": {
        "zh-CN": "样本数已够，继续累积观察趋势变化",
        "zh-TW": "樣本數已夠，繼續累積觀察趨勢變化",
        "en":    "Sufficient samples accumulated, continue monitoring trend",
        "ja":    "十分なサンプル数です。トレンドの変化を監視し続けます",
    },
    "record_error.need_more": {
        "zh-CN": "还需 {need} 次同类错误才能达到检测阈值 (≥5)",
        "zh-TW": "還需 {need} 次同類錯誤才能達到檢測閾值 (≥5)",
        "en":    "Need {need} more error(s) of this type to reach detection threshold (≥5)",
        "ja":    "検出閾値(≥5)に達するまで、あと {need} 回の同種エラーが必要です",
    },

    "install.no_provider": {
        "zh-CN": "无可用 LLM provider，观察者以兜底模式运行（不阻塞）",
        "zh-TW": "無可用 LLM provider，觀察者以兜底模式運行（不阻塞）",
        "en":    "No LLM provider available, observer running in fallback mode (non-blocking)",
        "ja":    "利用可能なLLMプロバイダーがありません。オブザーバーはフォールバックモードで実行中（非ブロッキング）",
    },
}


def get_all_strings_for_lang(lang: str) -> Dict[str, str]:
    """取某一语言的全部翻译（供调试用）。"""
    result = {}
    for key, translations in _STRINGS.items():
        result[key] = translations.get(lang, translations.get("zh-CN", key))
    return result
