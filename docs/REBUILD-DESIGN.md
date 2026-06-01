# T.R.E.E. 重建设计文档（新架构权威蓝本）

> **定位**：在**新工作区从零重建**的唯一权威设计。完全抛弃旧实现的代码包袱，只保留：
> 1. **OCR 接口不变**（`ingest/ocr_engine.py` + extractors，远程 PaddleOCR-VL-1.6）。
> 2. **Embedding 接口不变**（Qwen3-Embedding-4B 本地服务 + Qdrant 向量库）。
> 3. **设计理念不变**（资料驱动 / 以考促写 / DAG 调度 / 忠实性优先）。
>
> **硬约束**：每个代码文件不宜过大（**软上限 ~400 行**，超了按职责拆分）。旧版 `cli.py 2910` / `knowledge_nodes.py 2204` / `planning_graph.py 1992` 这种巨型文件不允许再出现。
>
> 旧实现的完整审阅见同目录 [DESIGN.md](DESIGN.md)（仅作"要保留的接口/prompt"参考，**不要照抄其 planner 算法**）。

---

## 0. 与旧架构最关键的区别

| 环节 | 旧架构（要抛弃） | 新架构 |
|---|---|---|
| 结构化 | OCR → Archivist 清洗 → 单独的 cut-plan → **Inventory AI 分组(KnowledgeGroup)** | OCR → **Archivist 直接切 MTU 并产出 命名/关键词/摘要** |
| 节点生成 | knowledge_nodes.py **union-find 聚类 + AI merge review**（2204 行） | **dagger 一次性合并重复 MTU → canonical 节点** |
| 建图 | planning_graph.py **MST / 增量森林 / 亲和度打分**（1992 行） | **dagger 用 名/词/摘 一次性输出 DAG 边**（几乎无算法） |
| 建图依赖 | 依赖 RAG embedding 的 overlap 打分 | **只依赖 MTU 元数据，先建 DAG 再 embedding** |
| Branch | branches.py 复杂切割（1383 行） | **确定性程序简单切**（~150 行） |
| examiner 循环 | — | **基本不变** |

**一句话**：把"重算法"换成"两个 LLM agent（archivist 切+标注、dagger 合并+连边）+ 少量确定性程序"。Planner 代码体量从 ~7000 行降到预计 ~1000 行。

---

## 1. 新端到端数据流

```text
materials/<collection>/<file>            用户原始资料
  │
  ▼  ① OCR（接口不变）
ingest/ocr_engine + extractors           PaddleOCR-VL-1.6 → 原始 Markdown
  │
  ▼  ② Archivist agent
agents/archivist                         清洗 + 切成 MTU(最小可教学单元)
                                         每个 MTU 输出: title 命名 / keywords 关键词 /
                                         summary 摘要 / line_range / unit_kind / text
  │   产物: planner/mtus.json (envelope)
  ▼  ③ Dagger agent（新）
agents/dagger                            输入: 全部 MTU 的 {id,title,keywords,summary,
                                                          collection,source_order}
                                         一次性全局输出:
                                           - canonical KnowledgeNode[]（合并重复 MTU）
                                           - DAG edges[]（prerequisite / order）
  │   产物: planner/knowledge-nodes.json + knowledge-dag.json (envelope)
  ▼  ④ Branch 切割（确定性程序）
planner/branches.py                      DAG → 线性 KnowledgeBranch[]
  │   产物: planner/knowledge-branches.json (envelope)
  ▼  ⑤ RAG 建库（Qwen3-4B embedding，接口不变）
rag/client + rag/embed                   每个 MTU.text 嵌入 Qdrant（content_kind=source）
                                         embedding 完成后删除中间 Markdown
  │
  ▼  ⑥ BranchRun 调度 + Examiner/Student/Writer 循环（基本不变）
engine/orchestrator + branch_run         Step 0→1→2→3→4
  │
  ▼
outputs/<tree_id>/<branch_id>/<NN>.<title>.md   满分通过入库 + finished RAG
```

**关键顺序变化**：DAG 在 embedding **之前**、纯靠 MTU 元数据构建。Embedding 只服务于 examiner/student/writer 的检索，不再参与建图。这让建图与向量库解耦，更简单、可独立测试。

---

## 2. 目标模块布局（每文件 < ~400 行）

```text
tree_engine/tree/
├── cli/
│   ├── app.py                 # typer app 装配（薄）
│   ├── commands/
│   │   ├── lifecycle.py        # start/stop/quit/run/resume/continue
│   │   ├── ingest.py           # ingest
│   │   ├── inspect.py          # status/progress/materials/logs/doctor/clean
│   │   ├── rag.py              # rag status|inventory|nodes|graph|search
│   │   └── config_cmd.py       # setup/models/prompts
│   ├── repl.py                 # TREE> 交互循环 + 强制退出处理
│   └── dashboard/
│       ├── model.py            # 从 state/progress 构建看板数据模型
│       ├── panels.py           # rich 面板（source/planner/branch slots）
│       └── dag_view.py         # 编号 DAG 的 ascii/着色渲染
├── engine/
│   ├── orchestrator.py         # run() 主循环（薄）
│   ├── branch_run.py           # 单 BranchRun 的 Step 0→4
│   └── ingest_driver.py        # 资料增量摄入 + embedding 编排
├── agents/
│   ├── base.py                 # 共用：调用 LLM、格式重试、解析钩子
│   ├── archivist.py            # 清洗 + 切 MTU + 命名/关键词/摘要
│   ├── dagger.py               # 合并 canonical 节点 + 建 DAG（新）
│   ├── examiner.py
│   ├── student.py
│   ├── writer.py
│   ├── parsers.py              # 各 agent 输出解析（section / JSON）
│   └── prompts/                # 每个角色一份 prompt（archivist/dagger/examiner/student/writer）
├── planner/
│   ├── pipeline.py             # 编排 ②③④ + envelope 落盘 + 增量 hash
│   ├── models.py               # MTU / KnowledgeNode / DagEdge / Branch 的 pydantic 模型
│   ├── dag.py                  # dagger 调用包装 + DAG 校验（断环/孤儿/传递归约）
│   ├── branches.py             # 确定性 DAG→branch 切割
│   ├── schedule.py             # ready branch 调度（写 pipeline-state）
│   ├── store.py                # envelope() / artifact_hash() / write_json_atomic()
│   └── ids.py                  # prefixed_id() 稳定 ID + 文本归一化
├── ingest/
│   ├── pipeline.py             # extract → archivist → MTU
│   ├── ocr_engine.py           # ★ 接口不变（远程 PaddleOCR）
│   └── extractors/             # pdf / image / docx / presentation（迁移）
├── rag/
│   ├── client.py               # ★ Qdrant 接口不变（index_file/query/scroll/delete）
│   ├── chunker.py              # 精简：MTU → chunk（MTU 边界即 chunk 边界）
│   ├── embed.py                # ★ Qwen3-4B 客户端不变
│   └── server.py               # ★ 本地 embedding 服务不变
├── model/client.py             # 多角色 AsyncOpenAI（新增 dagger 角色）+ 降级
├── state/
│   ├── models.py               # PipelineState / BranchExecution / BranchRun / 迭代态
│   └── manager.py              # StateManager（load/save/find_in_progress）
├── io/                         # paths / file_ops / git_ops / source_ops
├── observability/              # progress / logger / retry / limiter
└── config.py                   # 分角色配置（新增 DAGGER_*）+ env 加载
```

**角色集合**：`examiner / student / writer / archivist / dagger`（共 5 个）。config 与 LLM client 都要加 `dagger`。

---

## 3. 数据模型（`planner/models.py`，pydantic）

```python
class MTU(BaseModel):                  # ② Archivist 产出的最小可教学单元
    mtu_id: str                        # prefixed_id("mtu", [collection, file, start, end])
    collection: str
    source_file: str
    line_range: tuple[int, int]        # 在清洗后 Markdown 中的行范围（含端点）
    title: str                         # 命名
    keywords: list[str]                # 关键词
    summary: str                       # 摘要
    unit_kind: str                     # concept | example | exercise | misconception | ...
    source_order_index: int            # 同 collection 内的顺序，用于 source 顺序边
    # text 不入 mtus.json（体积考虑）；embedding 时按 line_range 从 Markdown 现读

class KnowledgeNode(BaseModel):        # ③ Dagger 合并后的 canonical 节点
    node_id: str
    title: str
    member_mtu_ids: list[str]          # 被合并进来的 MTU（≥1）
    keywords: list[str]                # 合并去重
    summary: str                       # 合并/改写
    collections: list[str]
    source_order_index: int            # 取成员 MTU 的最小顺序

class DagEdge(BaseModel):
    from_node_id: str
    to_node_id: str
    relation: str                      # "prerequisite"（硬先修） | "order"（软顺序）
    confidence: float

class KnowledgeBranch(BaseModel):      # ④ 程序确定性切出
    branch_id: str
    node_ids: list[str]                # 线性顺序
    coverage_node_ids: list[str]
    start_node_id: str
    end_node_id: str
    upstream_branch_ids: list[str]
    downstream_branch_ids: list[str]
    display_order: int
```

执行态模型（`state/models.py`）**直接沿用旧版**（已干净）：`PipelineState / BranchExecutionRecord / BranchRunRecord / CoverageSnapshot / ExamSections / AuditResult / WriterResult / IterationState`。详见 [DESIGN.md §3.2](DESIGN.md)。

---

## 4. Stage 契约

### ② Archivist：清洗 + 切 MTU + 标注
- **输入**：单个文件的 OCR Markdown。
- **职责**：
  1. 去非教学噪声（页眉页脚/页码/版权/水印/图片残链）、规范标题层级（沿用旧 archivist 清洗规则）。
  2. 按**最小可教学单元**切分：一个 MTU 是能被独立讲授和考核的最小连贯片段；不按公式/性质/例题/习题拆同一概念，但比"整章"细。
  3. 为每个 MTU 输出 `title / keywords / summary / line_range / unit_kind`。
- **输出**：严格 JSON。
  ```json
  {
    "units": [
      {"start_line": 1, "end_line": 28, "title": "化学平衡状态",
       "keywords": ["可逆反应","正逆速率相等","动态平衡"],
       "summary": "定义化学平衡状态及其动态特征……",
       "unit_kind": "concept"}
    ],
    "skipped_ranges": [{"start_line": 29, "end_line": 30, "reason": "page_footer"}]
  }
  ```
- **约束**：每一行必须被某 unit 或 skipped_range 覆盖，不静默丢行（程序侧校验，过碎/漏行触发 repair，次数可配）。
- **落盘**：所有文件的 MTU 汇总进 `planner/mtus.json`（envelope）。清洗后 Markdown 暂存 `runtime/source/<collection>/<file>.md`，embedding 后删。

### ③ Dagger：合并 canonical 节点 + 建 DAG（新 agent）
- **输入**：全部 MTU 的轻量元数据列表（**不含 text**，省 token）：
  ```json
  [{"mtu_id":"mtu:...","title":"...","keywords":[...],"summary":"...",
    "collection":"课件","source_order_index":3}]
  ```
- **职责**（一次性全局，LLM 完成，几乎无程序算法）：
  1. **合并**：把跨文件指同一知识点的 MTU 合并为一个 canonical `KnowledgeNode`（如"讲义里的化学平衡"+"习题里的化学平衡考点"）。
  2. **连边**：基于名/词/摘判断先修关系，输出 `prerequisite`（硬）与 `order`（软顺序）边。
- **输出**：严格 JSON `{ "nodes": [KnowledgeNode...], "edges": [DagEdge...] }`，每个 MTU 必须恰好归属一个 node。
- **程序侧校验（`planner/dag.py`，确定性、轻量）**：
  - 每个 MTU 被覆盖且仅一次；node_id 引用存在。
  - **断环**：检测并删除最弱（低 confidence）回边，保证 DAG。
  - 可选传递归约（删冗余先修边）。
  - 校验失败 → 一次 AI 修复或确定性兜底（按 source_order 串成单链）。
- **落盘**：`planner/knowledge-nodes.json` + `planner/knowledge-dag.json`（envelope）。
- **超大课程兜底**：决定为"一次性全局建图"。程序需在喂入前估算 token；若超模型上限，**降级**为按 collection 分批建局部图再做一次全局合并（保留为 fallback 路径，不作默认）。

### ④ Branch 切割（确定性，`planner/branches.py`，沿用旧 v2 `build_branches` 思路）
- branch 起点 = 入度 0 / 入度 >1（merge 点）/ branch 点（出度 >1）的子节点。
- 从起点沿"出度=1 且下游入度=1"链走到底成一条线性 branch。
- 算 coverage_node_ids、upstream/downstream 链接、display_order。
- 落盘 `planner/knowledge-branches.json`（envelope）。

### ⑤ RAG 建库（Qwen3-4B，接口不变）
- 对每个 MTU：按 `line_range` 从 Markdown 取 `text` → `RAGClient.index_file(... content_kind="source", source_collection=collection, doc_id=mtu_id)`。
- MTU 即天然 chunk 边界；`chunker.py` 仅在单个 MTU 超 token 预算时再切。payload 带 `mtu_id / node_id / title / keywords / collection / line_range`。
- embedding 串行（`SOURCE_EMBEDDING_CONCURRENCY=1`），完成后删中间 Markdown。
- finished outputs 与 draft 的 RAG 规则、三命名空间（source/finished/draft）**完全沿用**（见 [DESIGN.md §5.3](DESIGN.md)）。

### ⑥ BranchRun 循环（基本不变，见 §6）

---

## 5. Planner 编排（`planner/pipeline.py`）

```python
def rebuild_planner(root) -> Summary:
    manifest = scan_materials(root)              # 增量指纹，决定哪些文件要重跑
    mtus     = collect_mtus(root, manifest)      # ② 读/建各文件 MTU（archivist），envelope
    dag      = build_dag(mtus)                    # ③ dagger 合并+连边 → 校验，envelope
    branches = build_branches(dag)                # ④ 确定性切，envelope
    return summary(manifest, mtus, dag, branches)
```

- **全产物 envelope 化**：`{schema, inputs:[{path,hash}], diagnostics, data, algorithm_versions}`。靠 `artifact_hash` 比对输入决定是否跳过重建（增量）。
- **运行时与 `tre planner rebuild` 走同一条 `rebuild_planner`**（不再有旧版那种"运行时一套、CLI 一套"的割裂）。
- `engine/orchestrator.run()` 在无 in_progress BranchRun 时调用 `rebuild_planner` 刷新 artifacts，再调度 ready branch。

---

## 6. Examiner / Student / Writer 循环（沿用，改动最小）

主循环 `engine/orchestrator.run()` 与单 BranchRun `engine/branch_run.py` 的 **Step 0→4 逻辑、Prior Scope/CoverageSnapshot 可见性模型、PASS 条件、迭代上限** 全部沿用旧设计（见 [DESIGN.md §7](DESIGN.md)）。仅有的适配：

1. **节点术语统一为 canonical `KnowledgeNode`**：examiner 的 `Covered_Node_IDs` 引用 dagger 节点 id；branch span = 一条 branch 内连续的 canonical 节点。
2. **Prior Scope**：DAG ancestor closure 现在跑在 dagger 的 DAG 上（节点更少、更干净）。
3. **examiner 检索**：source RAG 现按 `node_id`/`mtu_id`/`collection` 过滤；anti-duplication 仍比对 finished-output RAG。
4. **四个 agent 的 prompt 几乎原样迁移**（examiner/student/writer/archivist 的清洗部分）。archivist 的"cut plan"prompt 演化为本文 §4② 的 MTU 契约；新增 dagger prompt。

> ⚠️ **必须保留的产品灵魂**：examiner/student/writer 三个 prompt 里凝结的 faithfulness（Knowledge Bleed 判定）、anti-duplication、branch-span 边界、零基础学生证据链规则——**几乎可逐字迁移**，是重建最高价值资产，不要重写。

---

## 7. 配置与接口保留清单

### 7.1 必须保持不变的接口
- **OCR**：`ingest/ocr_engine.py::OCREngine`、`optionalPayload`、上传节流/并发、`PADDLEOCR_*` 配置项、>99 页 PDF 切分拼接。
- **Embedding**：`rag/embed.py`（Qwen3-Embedding-4B-Q8_0，OpenAI 兼容 `/v1/embeddings`，端口 8788）、`rag/server.py`（本地 llama-cpp 服务，全局共享于 `~/.tree/services`）、`rag/client.py::RAGClient`（Qdrant 嵌入式，COSINE，dim 2560，`index_file/query` 签名）。

### 7.2 配置（`config.py`，新增 dagger）
- 加载顺序：`~/.tree/config.env` → `.env` → `.tree/config.env`（空值不覆盖）。
- 角色：`EXAMINER_ / STUDENT_ / WRITER_ / ARCHIVIST_ / DAGGER_` 各自 `_API_KEY/_BASE_URL/_MODEL`，回退 `LLM_*`。
- 关键旋钮（沿用 + 新增）：
  - 摄入：`SOURCE_INGEST_CONCURRENCY / SOURCE_OCR_CONCURRENCY / SOURCE_OCR_UPLOAD_INTERVAL_SEC / SOURCE_OCR_PDF_MAX_PAGES_PER_JOB / SOURCE_EMBEDDING_CONCURRENCY`。
  - Archivist：`ARCHIVIST_MTU_CUT_TIMEOUT_SEC / ARCHIVIST_MTU_REPAIR_ATTEMPTS`（替代旧 cut-plan 旋钮）。
  - Dagger：`DAGGER_BUILD_TIMEOUT_SEC / DAGGER_REPAIR_ATTEMPTS / DAGGER_MAX_NODES_PER_CALL`（分批 fallback 阈值）。
  - 循环：`MAX_ITERATIONS / MAX_RETRIES / MAX_FORMAT_RETRIES / MAX_ACTIVE_BRANCH_RUNS / MAX_EXAMINER_SPAN_NODES`。
  - 降级：`PRO_DEGRADATION_THRESHOLD / PRO_DEGRADATION_COOLDOWN_SEC`。

### 7.3 工作区目录（精简）
```text
my-course/
├── materials/<collection>/...
├── outputs/<tree_id>/<branch_id>/<NN>.<title>.md
└── .tree/
    ├── config.env
    └── runtime/
        ├── source/<collection>/<file>.md      # 清洗后中间 Markdown，embed 后删
        ├── drafts/<tree_id>/<branch_id>/...    # 不入 RAG
        ├── rag-store/                          # Qdrant
        ├── planner/
        │   ├── material-manifest.json
        │   ├── mtus.json
        │   ├── knowledge-nodes.json
        │   ├── knowledge-dag.json
        │   └── knowledge-branches.json
        ├── pipeline-state.json                 # 权威执行态
        ├── knowledge-ledger.json               # 权威 finished 记录
        ├── progress.json
        └── pipeline-temp/trace.jsonl
~/.tree/{config.env, services/}                 # 全局配置 + 共享 embedding 服务
```

---

## 8. 技术栈

与旧版一致：Python ≥3.12 / typer / pydantic / rich / structlog；openai SDK；RAG 可选依赖 `llama-cpp-python / huggingface-hub / qdrant-client / fastapi / uvicorn`；文档提取 `python-docx / python-pptx / pypdf`。打包 `tree-engine`，entry `tre`/`tree-run`。验证基线：`pytest && ruff check && compileall`。

---

## 9. 增量重建路线（每步可运行、可测）

1. **骨架**：`config.py / io/paths / state/models+manager / observability / store.py / ids.py`。迁移即可，写最小单测。
2. **OCR + 提取**：迁移 `ingest/ocr_engine + extractors + ingest/pipeline`（接口不变）。
3. **RAG**：迁移 `rag/embed + rag/server + rag/client`，精简 `rag/chunker`（MTU 边界）。本地起 embedding 服务跑通 index/query。
4. **Archivist → MTU**：`agents/archivist + agents/prompts/archivist`，产 `mtus.json`。单测：给定 Markdown → 行全覆盖、MTU 元数据齐全。
5. **Dagger → DAG**：`agents/dagger + planner/dag.py`，产 `knowledge-nodes/dag.json`。单测：合并重复 MTU、断环、孤儿校验、token 超限走分批 fallback。
6. **Branch + schedule**：`planner/branches.py + schedule.py + pipeline.py`。单测：DAG→线性 branch、ready 调度。
7. **Agents 循环**：迁移 `examiner/student/writer` + prompts + `model/client`（加 dagger 角色）+ `engine/branch_run`。
8. **Engine 编排**：`engine/orchestrator + ingest_driver`，串起 ②~⑥。
9. **CLI + 看板**：`cli/` 三分（commands / repl / dashboard）+ `doctor`。
10. **端到端验收**：真实资料 → `tre start` → `tre watch` → 检查 outputs 与 DAG 看板。

每步跑验证基线，保持仓库可运行。

---

## 10. 给重建者的提醒（避免重蹈覆辙）
- **不要**把渲染/纯函数塞进 `cli.py` 或 `engine` —— 看板独立成 `cli/dashboard/`，工具函数下沉 io/服务层。
- **不要**让程序再做图算法 —— 合并与连边是 dagger 的活，程序只做"校验 + 断环 + 切 branch"。
- **不要**保留旧文件名兼容 / 双 planner 并存 —— 单一 schema、单一 planner 路径。
- **保留** envelope + input-hash 增量、确定性优先 + AI 仅审边界、Prior Scope 可见性模型、三命名空间 RAG、Examiner 降级。
- 文件超 ~400 行先想"该拆成哪几个职责"，再写。

---

*本文件是新工作区重建的唯一权威蓝本。旧实现细节与接口原文见 [DESIGN.md](DESIGN.md)。*
