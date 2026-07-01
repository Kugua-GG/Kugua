# CLAUDE.md — Desktop 项目本地配置

> **全局框架**: 所有 Chat 已自动加载 `C:/Users/Administrator/.claude/CLAUDE.md`（v4.0 多Agent系统 + 29 Skills + Impeccable 设计规则）
> **本文件**: 仅包含 Desktop 目录特有的 LlamaIndex 本地环境

---

## LlamaIndex 环境

本机已部署 LlamaIndex MCP Server，提供本地文档索引和语义检索。

### 已安装版本
| 组件 | 版本 |
|------|------|
| llama-index | 0.14.22 |
| llama-index-llms-openai-like | 0.7.2 |
| llama-index-embeddings-huggingface | 0.7.0 |
| sentence-transformers | 5.5.1 |
| mcp | 1.27.2 |

### MCP 工具（6个）
| 工具 | 用途 |
|------|------|
| `ingest_directory` | 摄入目录 → 构建向量索引 |
| `query_index` | 自然语言查询索引 |
| `list_indexes` | 列出所有索引 |
| `add_documents` | 追加文档到已有索引 |
| `delete_index` | 删除索引 |
| `get_index_info` | 查看索引元信息 |

### LLM 配置
```python
from llama_index.llms.openai_like import OpenAILike
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.core import Settings

Settings.llm = OpenAILike(
    model="deepseek-v4-pro",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    api_base="https://api.deepseek.com/v1",
    temperature=0.1,
    max_tokens=4096,
    is_chat_model=True,
)
Settings.embed_model = HuggingFaceEmbedding(
    model_name="BAAI/bge-small-zh-v1.5",
    embed_batch_size=32,
)
```

### 核心 API 速查
```python
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
docs = SimpleDirectoryReader(input_dir="./docs", recursive=True).load_data()
index = VectorStoreIndex.from_documents(docs)
index.storage_context.persist(persist_dir="./index_store/my_index")
```

### 嵌入模型备选
| 模型 | 场景 | 大小 |
|------|------|------|
| `BAAI/bge-small-zh-v1.5` | 中文（默认） | ~100MB |
| `BAAI/bge-small-en-v1.5` | 英文 | ~130MB |
| `BAAI/bge-large-zh-v1.5` | 中文高精度 | ~600MB |
| `intfloat/multilingual-e5-small` | 多语言轻量 | ~200MB |

### 项目文件路径
- MCP Server: `C:/Users/Administrator/Desktop/llamaindex-mcp-server/server.py`
- 索引存储: `C:/Users/Administrator/Desktop/llamaindex-mcp-server/index_store/`
- MCP 配置: `C:/Users/Administrator/Desktop/.mcp.json`

---

## Desktop 本地多Agent资源

> 全局状态文件位于 `C:/Users/Administrator/.claude/.codex/artifacts/`（所有 Chat 共享）
> 以下为 Desktop 项目本地的可执行脚本和子代理定义：

- Worker Runner: `C:/Users/Administrator/Desktop/.codex/worker_runner.py`
- Checker Runner: `C:/Users/Administrator/Desktop/.codex/checker_runner.py`
- 子代理指令: `C:/Users/Administrator/Desktop/.claude/subagents/worker.md`, `checker.md`
- Desktop 本地状态: `C:/Users/Administrator/Desktop/.codex/artifacts/`（项目专属任务副本）
