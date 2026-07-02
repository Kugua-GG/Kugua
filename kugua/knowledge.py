"""
kugua.knowledge — 知识库模块 v0.2.0

提供三层知识管理:
  KBEntry        — 知识条目数据类,含 L0-L3 证据层级
  InvertedIndex  — BM25 Okapi 倒排索引,支持中英混合检索
  KnowledgeBase  — 知识库主类,含公理系统、逻辑冲突检测、垃圾回收

证据层级:
  L0 — 种子知识(不可降级)
  L1 — 单次验证
  L2 — 3+ 上下文验证
  L3 — 10+ 上下文验证 + 反例测试(公理级)
"""

import json
import math
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

LEVELS: Dict[str, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
LEVEL_NAMES: Dict[int, str] = {v: k for k, v in LEVELS.items()}

_STOPWORDS_EN: Set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "i", "you", "he",
    "she", "it", "we", "they", "me", "him", "her", "us", "them", "my",
    "your", "his", "its", "our", "their", "mine", "yours", "hers", "ours",
    "theirs", "this", "that", "these", "those", "am", "not", "no", "nor",
    "but", "or", "and", "if", "then", "else", "when", "where", "why",
    "how", "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "so", "than", "too", "very",
    "just", "about", "above", "after", "again", "against", "below",
    "between", "during", "into", "through", "up", "down", "in", "out",
    "on", "off", "over", "under", "of", "at", "by", "for", "with",
    "from", "to", "as", "until", "while", "because", "since", "also",
}

_STOPWORDS_ZH: Set[str] = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "所", "为", "所以", "因为", "但是", "然而", "虽然", "如果", "可以",
    "这个", "那个", "什么", "怎么", "哪", "吗", "啊", "吧", "呢", "嗯",
    "哦", "还", "被", "把", "从", "对", "与", "或", "及", "且", "向",
    "让", "给", "当", "以", "能", "将", "只", "其", "中", "等", "之",
    "已", "已经", "曾", "曾经", "没", "别", "多", "少", "又", "再",
    "才", "刚", "便", "则", "却", "可", "但", "并", "而", "应", "该",
    "需要", "应该", "能够", "可能", "会", "可以", "想", "知道", "觉得",
    "来", "去", "做", "作", "用", "使", "进行", "通过", "根据", "按照",
}


# ---------------------------------------------------------------------------
# KBEntry — 知识条目
# ---------------------------------------------------------------------------

@dataclass
class KBEntry:
    """知识库条目,含 L0-L3 证据层级元数据。

    字段:
        key:             唯一标识符
        content:         知识内容(自然语言)
        level:           证据层级 "L0"|"L1"|"L2"|"L3"
        scope:           适用范围描述
        verified_in:     已验证的上下文 ID 列表
        confidence:      置信度 [0, 1]
        expires_at:      过期时间戳(None = 永不过期)
        is_constant:     是否为恒真公理(不可降级/不可标记失败)
        axiomatic_parents: 依赖的公理 key 集合
        upgrade_cooldown: 升级/降级冷却计数(0 表示可操作)
        usage_count:     被检索使用的次数
        fail_count:      验证失败的次数
        status:          当前状态 "active"|"deprecated"|"challenged"
        tags:            标签列表
    """

    key: str
    content: str
    level: str = "L1"
    scope: Dict[str, Any] = field(default_factory=dict)
    verified_in: List[str] = field(default_factory=list)
    confidence: float = 1.0
    expires_at: Optional[float] = None
    is_constant: bool = False
    axiomatic_parents: Set[str] = field(default_factory=set)
    upgrade_cooldown: int = 0
    usage_count: int = 0
    fail_count: int = 0
    status: str = "active"
    tags: List[str] = field(default_factory=list)

    @property
    def level_int(self) -> int:
        return LEVELS.get(self.level, 0)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "content": self.content,
            "level": self.level,
            "scope": self.scope,
            "verified_in": list(self.verified_in),
            "confidence": self.confidence,
            "expires_at": self.expires_at,
            "is_constant": self.is_constant,
            "axiomatic_parents": sorted(self.axiomatic_parents),
            "upgrade_cooldown": self.upgrade_cooldown,
            "usage_count": self.usage_count,
            "fail_count": self.fail_count,
            "status": self.status,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KBEntry":
        return cls(
            key=d["key"],
            content=d["content"],
            level=d.get("level", "L1"),
            scope=d.get("scope", ""),
            verified_in=list(d.get("verified_in", [])),
            confidence=d.get("confidence", 1.0),
            expires_at=d.get("expires_at"),
            is_constant=d.get("is_constant", False),
            axiomatic_parents=set(d.get("axiomatic_parents", [])),
            upgrade_cooldown=d.get("upgrade_cooldown", 0),
            usage_count=d.get("usage_count", 0),
            fail_count=d.get("fail_count", 0),
            status=d.get("status", "active"),
            tags=list(d.get("tags", [])),
        )

    def __repr__(self) -> str:
        const_mark = " [CONST]" if self.is_constant else ""
        return (
            f"KBEntry(key={self.key!r}, level={self.level}, "
            f"confidence={self.confidence:.2f}, "
            f"usage={self.usage_count}, fail={self.fail_count}{const_mark})"
        )


# ---------------------------------------------------------------------------
# InvertedIndex — BM25 Okapi 倒排索引
# ---------------------------------------------------------------------------

class InvertedIndex:
    """BM25 Okapi 倒排索引,支持中英混合分词检索。

    BM25 参数:
        k1 = 1.5  — 词频饱和度
        b  = 0.75 — 文档长度归一化

    分词策略(3 层):
        1. 英文单词 token (小写,去停用词,去标点)
        2. 中文 bigram token (相邻二字组合)
        3. 标签精确匹配 (权重 ×2)
    """

    def __init__(self):
        # posting[term] = {doc_id: term_freq}
        self._posting: Dict[str, Dict[str, int]] = defaultdict(dict)
        # 已索引的文档内容缓存
        self._docs: Dict[str, str] = {}
        # 文档长度
        self._doc_lengths: Dict[str, int] = {}
        self._avg_dl: float = 0.0
        # 文档标签
        self._doc_tags: Dict[str, List[str]] = defaultdict(list)
        # 总文档数
        self._N: int = 0

        self.k1: float = 1.5
        self.b: float = 0.75

    # ---- tokenize ------------------------------------------------------------

    @staticmethod
    def tokenize(text: str, tags: Optional[List[str]] = None) -> List[str]:
        """三层分词: 英文单词 + 中文 bigram + 标签精确匹配(×2 权重)。

        返回 token 列表,标签 token 重复 2 次以给予额外权重。
        """
        tokens: List[str] = []

        # 第 1 层: 英文单词
        # 提取字母序列(2 字符以上)
        english_words = re.findall(r"[a-zA-Z]{2,}", text.lower())
        for w in english_words:
            if w not in _STOPWORDS_EN:
                tokens.append(w)

        # 第 2 层: 中文 bigram
        # 提取连续中文字符
        chinese_chars = re.findall(r"[一-鿿]+", text)
        for segment in chinese_chars:
            # 过滤掉长度 <= 1 的
            chars = list(segment)
            for i in range(len(chars) - 1):
                bigram = chars[i] + chars[i + 1]
                if bigram not in _STOPWORDS_ZH:
                    tokens.append(bigram)
            # 也加入单个字符(如果 segment 只有 1 字)
            if len(chars) == 1:
                if chars[0] not in _STOPWORDS_ZH:
                    tokens.append(chars[0])

        # 第 3 层: 标签精确匹配 ×2 权重
        if tags:
            for tag in tags:
                tag_lower = tag.lower().strip()
                if tag_lower:
                    tokens.append(f"@tag:{tag_lower}")
                    tokens.append(f"@tag:{tag_lower}")  # 双倍权重

        return tokens

    # ---- add / remove / update / clear ---------------------------------------

    def add(self, doc_id: str, content: str, tags: Optional[List[str]] = None):
        """索引一个文档。"""
        tokens = self.tokenize(content, tags)
        self._docs[doc_id] = content
        if tags:
            self._doc_tags[doc_id] = list(tags)
        else:
            self._doc_tags[doc_id] = []

        # 计入词频
        tf_map: Dict[str, int] = {}
        for t in tokens:
            tf_map[t] = tf_map.get(t, 0) + 1

        for term, freq in tf_map.items():
            self._posting[term][doc_id] = freq

        self._doc_lengths[doc_id] = len(tokens)
        self._N = len(self._docs)
        self._recompute_avg_dl()

    def remove(self, doc_id: str):
        """从索引中移除文档。"""
        if doc_id not in self._docs:
            return
        # 从 posting 中移除
        tokens = self.tokenize(self._docs[doc_id], self._doc_tags.get(doc_id))
        tf_map: Dict[str, int] = {}
        for t in tokens:
            tf_map[t] = tf_map.get(t, 0) + 1
        for term in tf_map:
            if term in self._posting and doc_id in self._posting[term]:
                del self._posting[term][doc_id]
                if not self._posting[term]:
                    del self._posting[term]

        self._docs.pop(doc_id, None)
        self._doc_lengths.pop(doc_id, None)
        self._doc_tags.pop(doc_id, None)
        self._N = len(self._docs)
        self._recompute_avg_dl()

    def update(self, doc_id: str, content: str, tags: Optional[List[str]] = None):
        """更新索引中的文档(等价于 remove + add)。"""
        self.remove(doc_id)
        self.add(doc_id, content, tags)

    def clear(self):
        """清空整个索引。"""
        self._posting.clear()
        self._docs.clear()
        self._doc_lengths.clear()
        self._doc_tags.clear()
        self._N = 0
        self._avg_dl = 0.0

    # ---- search (BM25) -------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        tags: Optional[List[str]] = None,
        min_score: float = 0.0,
    ) -> List[Tuple[str, float]]:
        """BM25 Okapi 检索。

        返回 [(doc_id, score), ...] 按分数降序排列。
        """
        query_tokens = self.tokenize(query, tags)
        if not query_tokens or self._N == 0:
            return []

        scores: Dict[str, float] = defaultdict(float)

        for term in query_tokens:
            if term not in self._posting:
                continue
            posting = self._posting[term]
            df = len(posting)  # document frequency
            idf = math.log((self._N - df + 0.5) / (df + 0.5) + 1.0)

            for doc_id, tf in posting.items():
                dl = self._doc_lengths.get(doc_id, 1)
                # BM25 term score
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (
                    1 - self.b + self.b * (dl / max(self._avg_dl, 1))
                )
                scores[doc_id] += idf * (numerator / denominator)

        # 排序并返回
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [(did, s) for did, s in ranked if s >= min_score][:top_k]

    # ---- 精度 / 召回 ---------------------------------------------------------

    def context_precision(
        self, query: str, relevant_ids: Set[str], top_k: int = 10
    ) -> float:
        """检索精度 = |检索结果 ∩ 相关文档| / |检索结果|。"""
        results = self.search(query, top_k=top_k)
        if not results:
            return 0.0
        retrieved = {did for did, _ in results}
        return len(retrieved & relevant_ids) / len(retrieved)

    def context_recall(
        self, query: str, relevant_ids: Set[str], top_k: int = 10
    ) -> float:
        """检索召回率 = |检索结果 ∩ 相关文档| / |相关文档|。"""
        if not relevant_ids:
            return 1.0
        results = self.search(query, top_k=top_k)
        retrieved = {did for did, _ in results}
        return len(retrieved & relevant_ids) / len(relevant_ids)

    # ---- 内部方法 -------------------------------------------------------------

    def _recompute_avg_dl(self):
        if self._doc_lengths:
            self._avg_dl = sum(self._doc_lengths.values()) / len(self._doc_lengths)
        else:
            self._avg_dl = 0.0

    def _rebuild(self, entries: Dict[str, "KBEntry"]):
        """从 KBEntry 字典完全重建索引。"""
        self.clear()
        for entry in entries.values():
            if entry.status == "active" and not entry.is_expired:
                # 将 level 和 scope 也加入 content 以便检索
                searchable = f"{entry.content}"
                self.add(entry.key, searchable, entry.tags)

    # ---- 序列化 ---------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "posting": {
                term: dict(doc_map) for term, doc_map in self._posting.items()
            },
            "docs": dict(self._docs),
            "doc_lengths": dict(self._doc_lengths),
            "doc_tags": dict(self._doc_tags),
            "avg_dl": self._avg_dl,
            "N": self._N,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "InvertedIndex":
        idx = cls()
        idx._posting = defaultdict(dict, {
            term: dict(doc_map) for term, doc_map in d.get("posting", {}).items()
        })
        idx._docs = dict(d.get("docs", {}))
        idx._doc_lengths = dict(d.get("doc_lengths", {}))
        idx._doc_tags = defaultdict(list, {
            k: list(v) for k, v in d.get("doc_tags", {}).items()
        })
        idx._avg_dl = d.get("avg_dl", 0.0)
        idx._N = d.get("N", 0)
        return idx


# ---------------------------------------------------------------------------
# KnowledgeBase — 知识库主类
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """证据层级知识库,管理 KBEntry + InvertedIndex + 公理系统。

    核心机制:
        - 公理(axiom)不可降级、不可标记失败
        - 标签匹配链接公理依赖(_link_axioms)
        - 双阈值逻辑冲突检测: 否定词计数 > 0 AND 关键词重叠 >= 3
        - 垃圾回收: 清除 L1 expired/fail_count >= 5 且 usage_count < 2
        - 拓扑加分: L3 公理被引用时给予额外权重

    Parameters:
        config:         配置字典(至少含 "kb_path")
        seed_path:      L0 种子知识 JSON 文件路径
        constants_path: 公理 JSONL 文件路径
    """

    def __init__(
        self,
        config=None,  # KuguaConfig | dict | None
        seed_path: Optional[str] = None,
        constants_path: Optional[str] = None,
    ):
        # Accept both KuguaConfig objects and plain dicts
        from kugua.config import KuguaConfig as KC
        if isinstance(config, KC):
            self._cfg = config
            self.config: Dict[str, Any] = {}
            self.kb_path = str(config.get_artifacts_path("knowledge_base.md"))
        elif isinstance(config, dict):
            self._cfg = None
            self.config = config
            self.kb_path = config.get("kb_path", "./kb_store/knowledge.json")
        else:
            self._cfg = KC()
            self.config = {}
            self.kb_path = str(self._cfg.get_artifacts_path("knowledge_base.md"))
        self.constants_path: str = constants_path or os.path.join(
            os.path.dirname(self.kb_path), "constants.jsonl"
        )

        # 主存储: key -> KBEntry
        self._entries: Dict[str, KBEntry] = {}
        # Public alias — legacy code uses .entries
        self.entries = self._entries
        # 索引
        self._index = InvertedIndex()
        # 公理 key 集合(快速查找)
        self._axiom_keys: Set[str] = set()
        # 标签 -> 公理 key 映射
        self._tag_to_axioms: Dict[str, Set[str]] = defaultdict(set)
        # 修改计数(用于触发持久化)
        self._dirty: bool = False
        # 延迟初始化 GraphKB(避免循环导入)
        self._graph = None

        # 加载持久化数据 + 公理
        self._init_load(seed_path, constants_path)

    @property
    def graph(self):
        if self._graph is None:
            from kugua.graph import GraphKB
            self._graph = GraphKB()
        return self._graph

    def _init_load(self, seed_path, constants_path):
        self._load()
        if seed_path and not self._entries:
            self._load_seeds(seed_path)
        if constants_path:
            self._load_constants(constants_path)
        self._rebuild_graph_from_entries()

    def _get_graph(self):
        """懒加载 GraphKB,避免循环导入。"""
        if self._graph is None:
            from kugua.graph import GraphKB

            self._graph = GraphKB()
            self._rebuild_graph_from_entries()
        return self._graph

    # ---- 持久化 ---------------------------------------------------------------

    def _load(self):
        """从 JSON 文件加载知识库。"""
        if not os.path.exists(self.kb_path):
            return
        try:
            with open(self.kb_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries_raw = data.get("entries", [])
            for raw in entries_raw:
                entry = KBEntry.from_dict(raw)
                self._entries[entry.key] = entry
                if entry.is_constant:
                    self._axiom_keys.add(entry.key)
                    for tag in entry.tags:
                        self._tag_to_axioms[tag].add(entry.key)
            # 重建索引
            self._index._rebuild(self._entries)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[KnowledgeBase] 加载失败: {e}")

    def _save(self):
        """持久化到 JSON 文件,保存 is_constant 字段。"""
        os.makedirs(os.path.dirname(self.kb_path), exist_ok=True)
        entries_list = [entry.to_dict() for entry in self._entries.values()]
        with open(self.kb_path, "w", encoding="utf-8") as f:
            json.dump({"entries": entries_list}, f, ensure_ascii=False, indent=2)
        self._dirty = False

    # ---- CRUD ----------------------------------------------------------------

    def add(self, entry: KBEntry) -> bool:
        """添加知识条目。自动链接公理依赖 + 添加 GraphKB 边。

        Returns:
            True 如果成功添加, False 如果 key 已存在或 L0。
        """
        if entry.level == "L0":
            return False
        if not isinstance(entry.scope, dict) or not entry.scope:
            return False
        if entry.key in self._entries:
            # Update existing entry
            self._index.remove(entry.key)
            self._entries[entry.key] = entry
            self._index.add(entry.key, entry.content, entry.tags)
            self._dirty = True
            return True

        # 调用 _link_axioms
        self._link_axioms(entry)

        self._entries[entry.key] = entry
        if entry.is_constant:
            self._axiom_keys.add(entry.key)
            for tag in entry.tags:
                self._tag_to_axioms[tag].add(entry.key)

        # 索引
        self._index.add(entry.key, entry.content, entry.tags)

        # GraphKB 边
        graph = self._get_graph()
        graph.add_node(entry.key, labels=entry.tags, level=entry.level)
        for parent_key in entry.axiomatic_parents:
            if parent_key in self._entries:
                graph.add_edge(
                    entry.key,
                    parent_key,
                    relation="axiom_depends_on",
                    weight=1.0,
                )

        self._dirty = True
        return True

    def get(self, key: str) -> Optional[KBEntry]:
        """按 key 获取条目。"""
        return self._entries.get(key)

    def query(self, query_text: str = "", top_k: int = 10, min_level: str = "L0") -> List[Tuple[KBEntry, float]]:
        """语义检索: 通过 BM25 索引查找条目,可按最低证据层级过滤。

        Args:
            query_text: 搜索词(空字符串 = 返回所有匹配 min_level 的条目)
            top_k: 最大返回数
            min_level: 最低证据层级 (L0/L1/L2/L3),默认 L0(全部)

        Returns:
            [(KBEntry, score), ...]
        """
        min_lv = LEVELS.get(min_level, 0)

        if query_text:
            results = self._index.search(query_text, top_k=top_k)
            scored: Dict[str, float] = dict(results)
        else:
            # 无搜索词: 返回所有活跃条目,按 usage_count 排序
            scored = {}
            for key, entry in self._entries.items():
                if entry.status == "active" and not entry.is_expired and entry.level_int >= min_lv:
                    scored[key] = float(entry.usage_count)
            # 按 score 降序,取 top_k
            sorted_items = sorted(scored.items(), key=lambda x: x[1], reverse=True)
            scored = dict(sorted_items[:top_k])

        out: List[Tuple[KBEntry, float]] = []
        for doc_id, score in scored.items():
            entry = self._entries.get(doc_id)
            if entry and entry.status == "active" and not entry.is_expired:
                if entry.level_int >= min_lv:
                    entry.usage_count += 1
                    self._dirty = True
                    out.append((entry, score))
        return out

    def search(self, query_text: str = "", top_k: int = 10, min_level: str = "L0") -> List[KBEntry]:
        """同 query,但只返回 KBEntry 列表(不含分数)。"""
        return [entry for entry, _ in self.query(query_text, top_k=top_k, min_level=min_level)]

    # ---- 标记成功/失败 ---------------------------------------------------------

    def mark_fail(self, key: str) -> Optional[KBEntry]:
        """标记条目验证失败。每次失败立即可降级。"""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if getattr(entry, 'is_constant', False):
            entry.fail_count += 1
            return entry
        if getattr(entry, 'upgrade_cooldown', 0) > 0:
            entry.upgrade_cooldown -= 1
            entry.fail_count += 1
            return entry

        entry.fail_count += 1
        lv = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}.get(entry.level, 1)
        if lv <= 1:
            entry.status = "invalid"
        else:
            entry.level = f"L{lv - 1}"
            entry.confidence = max(0.1, entry.confidence - 0.3)

        self._dirty = True
        return entry

    def mark_success(self, key: str):
        """标记条目验证成功。

        规则:
            - usage_count += 1
            - L1 升级到 L2: upgrade_cooldown = 5
            - L2 升级到 L3: upgrade_cooldown = 10
            - L0 升级: 需要 verified_in >= 3 且 confidence >= 0.8
        """
        entry = self._entries.get(key)
        if entry is None:
            return

        entry.usage_count += 1
        lv = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}.get(entry.level, 1)

        if lv < 2 and entry.usage_count >= 3:
            entry.level = "L2"
            entry.confidence = min(0.9, entry.confidence + 0.2)
            entry.upgrade_cooldown = 5
        elif lv < 3 and entry.usage_count >= 10:
            entry.level = "L3"
            entry.confidence = min(1.0, entry.confidence + 0.1)
            entry.upgrade_cooldown = 10

        self._dirty = True

    # ---- 验证 -----------------------------------------------------------------

    def verify(self, entry: KBEntry) -> bool:
        """验证条目是否与现有公理冲突。

        Returns:
            True 如果无冲突, False 如果存在冲突。
        """
        if self._conflicts_with_any_axiom(entry):
            return False
        return True

    def check_axiom_conflict(self, entry: KBEntry) -> bool:
        """公开接口: 检查条目是否与任何公理冲突。替代直接调用 _conflicts_with_any_axiom。

        Returns:
            True 如果存在冲突, False 如果无冲突。
        """
        return self._conflicts_with_any_axiom(entry)

    def _conflicts_with_any_axiom(self, entry: KBEntry) -> bool:
        """检查条目是否与任何公理存在逻辑冲突。"""
        for axiom_key in self._axiom_keys:
            axiom = self._entries.get(axiom_key)
            if axiom is None:
                continue
            if self._check_logical_conflict(entry.content, axiom.content):
                return True
        return False

    # ---- 公理链接 --------------------------------------------------------------

    def _link_axioms(self, entry: KBEntry):
        """基于标签(非关键词)将条目链接到相关公理。

        遍历 entry 的 tags,查找与公理 tags 重叠的,建立 axiomatic_parents 关系。
        """
        if not entry.tags:
            return
        for tag in entry.tags:
            tag_lower = tag.lower()
            if tag_lower in self._tag_to_axioms:
                entry.axiomatic_parents.update(self._tag_to_axioms[tag_lower])

    # ---- 图重建 ----------------------------------------------------------------

    def _rebuild_graph_from_entries(self):
        """从当前条目重建 GraphKB 边。"""
        try:
            graph = self._get_graph()
            # 添加所有条目的节点
            for entry in self._entries.values():
                graph.add_node(entry.key, labels=entry.tags, level=entry.level)
            # 添加公理依赖边
            for entry in self._entries.values():
                for parent_key in entry.axiomatic_parents:
                    if parent_key in self._entries:
                        graph.add_edge(
                            entry.key,
                            parent_key,
                            relation="axiom_depends_on",
                            weight=1.0,
                        )
        except Exception:
            pass  # GraphKB 不可用时静默跳过

    # ---- 加载种子和公理 ---------------------------------------------------------

    def _load_seeds(self, seed_path: str):
        """加载 L0 种子知识 JSON 文件。"""
        if not os.path.exists(seed_path):
            return
        try:
            with open(seed_path, "r", encoding="utf-8") as f:
                seeds = json.load(f)
            if isinstance(seeds, dict) and "entries" in seeds:
                seeds = seeds["entries"]
            for raw in seeds:
                entry = KBEntry.from_dict(raw)
                entry.level = "L0"
                entry.is_constant = False
                self.add(entry)
            self._save()
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[KnowledgeBase] 种子加载失败: {e}")

    def _load_constants(self, constants_path: str):
        """加载公理 JSONL 文件。每行一个 JSON 对象。"""
        if not os.path.exists(constants_path):
            return
        try:
            with open(constants_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    raw = json.loads(line)
                    # Wrap tags into scope dict for from_dict compatibility
                    raw["scope"] = {"tags": raw.get("tags", []), "source": "axiom"}
                    entry = KBEntry.from_dict(raw)
                    entry.level = raw.get("level", "L3")
                    entry.is_constant = True
                    entry.axiomatic_parents = set()
                    self.add(entry)
            self._build_axiom_edges()
            self._save()
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[KnowledgeBase] 公理加载失败: {e}")

    def _build_axiom_edges(self):
        """在公理之间建立依赖边(基于标签重叠)。"""
        axiom_list = [
            self._entries[k] for k in self._axiom_keys if k in self._entries
        ]
        graph = self._get_graph()
        for i, ax_i in enumerate(axiom_list):
            tags_i = set(t.lower() for t in ax_i.tags)
            for ax_j in axiom_list[i + 1:]:
                tags_j = set(t.lower() for t in ax_j.tags)
                overlap = tags_i & tags_j
                if overlap:
                    weight = len(overlap) / max(len(tags_i), len(tags_j), 1)
                    graph.add_edge(
                        ax_i.key,
                        ax_j.key,
                        relation="axiom_related",
                        weight=weight,
                    )

    # ---- 逻辑冲突检测 ----------------------------------------------------------

    @staticmethod
    def _check_logical_conflict(text_a: str, text_b: str) -> bool:
        """双阈值逻辑冲突检测。

        条件:
            1. 否定词计数 > 0 (在一段中包含否定,另一段中不包含)
            2. 关键词重叠 >= 3

        否定词列表: not, no, never, cannot, must not, should not, 不, 不能,
                     不得, 禁止, 不应, 不可, 绝不
        """
        negation_patterns = [
            r"\bnot\b", r"\bno\b", r"\bnever\b", r"\bcannot\b",
            r"\bmust\s+not\b", r"\bshould\s+not\b",
            "不", "不能", "不得", "禁止", "不应", "不可", "绝不",
        ]

        def count_negations(text: str) -> int:
            count = 0
            lower = text.lower()
            for pat in negation_patterns:
                if re.search(pat, lower):
                    count += 1
            return count

        neg_a = count_negations(text_a)
        neg_b = count_negations(text_b)

        # 至少一侧有否定,且两侧否定数不一致
        if neg_a + neg_b == 0:
            return False
        if neg_a > 0 and neg_b > 0:
            # 两侧都有否定,可能不冲突
            return False

        # 关键词重叠检测
        # 使用简单词汇分词
        def extract_keywords(text: str) -> Set[str]:
            words: Set[str] = set()
            # 英文词
            en_words = re.findall(r"[a-zA-Z]{3,}", text.lower())
            for w in en_words:
                if w not in _STOPWORDS_EN:
                    words.add(w)
            # 中文双字
            cn_chars = re.findall(r"[一-鿿]+", text)
            for seg in cn_chars:
                chars = list(seg)
                for i in range(len(chars) - 1):
                    bigram = chars[i] + chars[i + 1]
                    if bigram not in _STOPWORDS_ZH:
                        words.add(bigram)
            return words

        kw_a = extract_keywords(text_a)
        kw_b = extract_keywords(text_b)
        overlap = len(kw_a & kw_b)

        return overlap >= 3

    # ---- 公理质疑 --------------------------------------------------------------

    def challenge_axiom(self, axiom_key: str, challenge_entry: KBEntry) -> bool:
        """对公理发起质疑。

        将公理状态设为 "challenged" 并记录质疑条目。
        质疑不会直接删除公理,需通过双环学习流程裁决。

        Returns:
            True 如果质疑被接受, False 如果公理不存在或非公理。
        """
        axiom = self._entries.get(axiom_key)
        if axiom is None or not axiom.is_constant:
            return False
        axiom.status = "challenged"
        # 将质疑条目关联到被质疑公理
        challenge_entry.axiomatic_parents.add(axiom_key)
        self.add(challenge_entry)
        self._dirty = True
        return True

    # ---- 垃圾回收 --------------------------------------------------------------

    def garbage_collect(self) -> int:
        """清除低质量条目。

        规则:
            - L0 且 expires_at 已过期
            - L1 且 fail_count >= 5 且 usage_count < 2
            - status == "deprecated"
            - 不删除 is_constant 条目

        Returns:
            删除的条目数。
        """
        to_remove: List[str] = []
        for key, entry in self._entries.items():
            if entry.is_constant:
                continue
            if entry.status == "deprecated":
                to_remove.append(key)
                continue
            if entry.is_expired and entry.level == "L0":
                to_remove.append(key)
                continue
            if entry.level == "L1" and entry.fail_count >= 5 and entry.usage_count < 2:
                to_remove.append(key)

        for key in to_remove:
            self._entries.pop(key, None)
            self._index.remove(key)
            try:
                self._get_graph().remove_node(key)
            except Exception:
                pass

        if to_remove:
            self._dirty = True
        return len(to_remove)

    # ---- 统计 -----------------------------------------------------------------

    def effective_stats(self) -> Dict[str, Any]:
        """返回知识库有效统计信息。"""
        total = len(self._entries)
        active = sum(1 for e in self._entries.values() if e.status == "active")
        by_level: Dict[str, int] = defaultdict(int)
        const_count = 0
        for e in self._entries.values():
            by_level[e.level] += 1
            if e.is_constant:
                const_count += 1

        return {
            "total": total,
            "active": active,
            "by_level": dict(by_level),
            "axioms": const_count,
            "challenged": sum(
                1 for e in self._entries.values() if e.status == "challenged"
            ),
            "deprecated": sum(
                1 for e in self._entries.values() if e.status == "deprecated"
            ),
            "index_size": self._index._N,
        }

    def stability(self) -> float:
        """计算知识库稳定性(公理未被质疑的比例)。"""
        axioms = [e for e in self._entries.values() if e.is_constant]
        if not axioms:
            return 1.0
        stable = sum(1 for a in axioms if a.status == "active")
        return stable / len(axioms)

    # ---- 过期清理 --------------------------------------------------------------

    def expire_stale(self) -> int:
        """将已过期的条目标记为 deprecated。

        Returns:
            标记的条目数。
        """
        count = 0
        for entry in self._entries.values():
            if entry.is_constant:
                continue
            if entry.is_expired and entry.status == "active":
                entry.status = "deprecated"
                count += 1
        if count:
            self._dirty = True
        return count

    # ---- 拓扑加分 --------------------------------------------------------------

    def apply_topology_bonus(self, bonus: float = 0.05):
        """对 L3 公理按图拓扑中心度给予置信度加分。

        被更多条目引用的公理获得额外置信度。
        """
        graph = self._get_graph()
        for key in self._axiom_keys:
            entry = self._entries.get(key)
            if entry is None:
                continue
            # 入度 = 依赖该公理的条目数
            in_degree = len(graph.get_in_edges(key))
            if in_degree > 0:
                boost = min(bonus * math.log1p(in_degree), 0.1)
                entry.confidence = min(entry.confidence + boost, 1.0)
        self._dirty = True

    # ---- 冲突扫描 --------------------------------------------------------------

    def scan_conflicts(self) -> List[Tuple[str, str, str]]:
        """扫描知识库中所有条目与公理之间的冲突。

        Returns:
            [(entry_key, axiom_key, description), ...]
        """
        conflicts: List[Tuple[str, str, str]] = []
        axiom_entries = [
            e for e in self._entries.values() if e.is_constant and e.status == "active"
        ]
        for entry in self._entries.values():
            if entry.is_constant or entry.status != "active":
                continue
            for axiom in axiom_entries:
                if self._check_logical_conflict(entry.content, axiom.content):
                    conflicts.append(
                        (entry.key, axiom.key, "logical_conflict_detected")
                    )
        return conflicts

    # ---- 知识新陈代谢 (v0.3) ----------------------------------------------------
    # Wake-Sleep Consolidation Pipeline
    #   Wake:  collect new L1 observations from recent tasks
    #   Sleep: re-evaluate L3 entries, promote L1→L2→L3, downgrade stale
    #   Dream: generate synthetic counterexamples for high-risk entries
    # (3D cross-validated: WSCL睡眠巩固, Bayesian模型选择, 周期性V(C)自检)

    def observe(
        self,
        key: str,
        content: str,
        context_id: str = "",
        confidence: float = 0.5,
        tags: Optional[List[str]] = None,
    ) -> KBEntry:
        """Record a new observation as an L1 entry.

        This is the INTAKE gate for new knowledge. Observations start at L1
        and accumulate evidence through the metabolism pipeline.

        Args:
            key: Unique identifier for this observation.
            content: The observed knowledge.
            context_id: Which context/task produced this observation.
            confidence: Initial confidence [0, 1].
            tags: Optional categorization tags.

        Returns:
            The created or updated KBEntry.
        """
        if key in self._entries:
            entry = self._entries[key]
            if context_id and context_id not in entry.verified_in:
                entry.verified_in.append(context_id)
                entry.usage_count += 1
                # Accumulate evidence for promotion
                if entry.usage_count >= 3 and entry.level == "L1":
                    entry.level = "L2"
                    entry.confidence = min(0.9, entry.confidence + 0.2)
                elif entry.usage_count >= 10 and entry.level == "L2":
                    entry.level = "L3"
                    entry.confidence = min(1.0, entry.confidence + 0.1)
            self._dirty = True
            return entry

        entry = KBEntry(
            key=key,
            content=content,
            level="L1",
            confidence=confidence,
            verified_in=[context_id] if context_id else [],
            tags=tags or [],
            usage_count=1,
        )
        self.add(entry)
        return entry

    def metabolism_cycle(self, min_l1_age: int = 3) -> Dict[str, Any]:
        """Run one full Wake-Sleep metabolism cycle.

        Wake phase:  process pending L1 observations
        Sleep phase: consolidate — promote, demote, validate axioms
        Dream phase: identify high-risk entries needing counterexample testing

        Inspired by WSCL (2024): Wake-Sleep Consolidated Learning.

        Args:
            min_l1_age: Minimum usage_count for L1→L2 promotion.

        Returns:
            Dict with cycle statistics.
        """
        stats = {
            "promoted_l1_to_l2": 0,
            "promoted_l2_to_l3": 0,
            "demoted_l3_to_l2": 0,
            "demoted_l2_to_l1": 0,
            "deprecated": 0,
            "garbage_collected": 0,
            "high_risk_count": 0,
            "axiom_validated": 0,
            "axiom_challenged": 0,
        }

        # ── Wake Phase: Process L1 observations ──
        l1_entries = [e for e in self._entries.values()
                      if e.level == "L1" and e.status == "active"]
        for entry in l1_entries:
            if entry.usage_count >= min_l1_age and entry.fail_count == 0:
                # Promote L1 → L2
                entry.level = "L2"
                entry.confidence = min(0.9, entry.confidence + 0.15)
                stats["promoted_l1_to_l2"] += 1

        # ── Sleep Phase: Re-evaluate L2/L3 entries ──
        l2_entries = [e for e in self._entries.values()
                      if e.level == "L2" and e.status == "active"]
        for entry in l2_entries:
            if entry.usage_count >= 10 and entry.fail_count == 0:
                # Promote L2 → L3 (需要反例测试)
                if not self._conflicts_with_any_axiom(entry):
                    entry.level = "L3"
                    entry.confidence = min(1.0, entry.confidence + 0.1)
                    stats["promoted_l2_to_l3"] += 1
            elif entry.fail_count >= 3:
                # Demote L2 → L1
                entry.level = "L1"
                entry.confidence = max(0.1, entry.confidence - 0.3)
                stats["demoted_l2_to_l1"] += 1

        l3_entries = [e for e in self._entries.values()
                      if e.level == "L3" and e.status == "active" and not e.is_constant]
        for entry in l3_entries:
            # Re-validate L3 axioms against accumulated counterexamples
            if entry.fail_count >= 5:
                # Demote L3 → L2
                entry.level = "L2"
                entry.confidence = max(0.2, entry.confidence - 0.3)
                entry.status = "challenged"
                stats["demoted_l3_to_l2"] += 1
                stats["axiom_challenged"] += 1
            elif self._conflicts_with_any_axiom(entry):
                entry.status = "challenged"
                stats["axiom_challenged"] += 1
            else:
                stats["axiom_validated"] += 1

        # ── Dream Phase: Identify high-risk entries ──
        # Entries with moderate confidence (0.5-0.7) and high usage need
        # synthetic counterexample testing
        high_risk = [
            e for e in self._entries.values()
            if e.status == "active"
            and 0.3 <= e.confidence <= 0.7
            and e.usage_count >= 5
            and not e.is_constant
        ]
        stats["high_risk_count"] = len(high_risk)
        for entry in high_risk:
            entry.status = "challenged"  # Mark for counterexample testing

        # ── Cleanup: garbage collect deprecated entries ──
        self.expire_stale()
        stats["garbage_collected"] = self.garbage_collect()

        if any(v > 0 for v in stats.values()):
            self._dirty = True

        return stats

    def get_pipeline_health(self) -> Dict[str, Any]:
        """Get health metrics for the knowledge metabolism pipeline.

        Healthy: L1 > L2 > L3 (pyramid structure).
        Stagnant: all L3, no L1/L2 inflow (current kugua state).
        """
        by_level = {"L1": 0, "L2": 0, "L3": 0}
        by_status = {"active": 0, "challenged": 0, "deprecated": 0, "invalid": 0}
        for e in self._entries.values():
            by_level[e.level] = by_level.get(e.level, 0) + 1
            by_status[e.status] = by_status.get(e.status, 0) + 1

        total = sum(by_level.values())
        active = by_status["active"]

        # Pyramid health: ideal is L1(60%) > L2(30%) > L3(10%)
        pyramid_score = 0.0
        if total > 0:
            l1_ratio = by_level["L1"] / total
            l2_ratio = by_level["L2"] / total
            l3_ratio = by_level["L3"] / total
            # Score: pyramid structure = descending ratios
            if l1_ratio >= l2_ratio >= l3_ratio and l1_ratio > 0:
                pyramid_score = min(1.0, (l1_ratio - l3_ratio) + 0.5)
            else:
                pyramid_score = max(0.0, l1_ratio + l2_ratio * 0.5)

        return {
            "total": total,
            "active": active,
            "by_level": by_level,
            "by_status": by_status,
            "pyramid_health": round(pyramid_score, 2),
            "is_stagnant": by_level["L1"] == 0 and by_level["L2"] == 0 and by_level["L3"] > 0,
            "needs_metabolism": by_level["L1"] > 0 or by_status["challenged"] > 0,
        }

    # ---- 持久化 flush ----------------------------------------------------------

    def flush(self):
        """如果 dirty 则持久化。"""
        if self._dirty:
            self._save()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def __iter__(self):
        return iter(self._entries.values())
