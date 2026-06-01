# T.R.E.E. 设计文档（重建蓝本）

> **用途**：本文件是对当前 `TREE` 工作区的完整设计抽取，供你在新工作区从零重建使用。它既描述"系统应该是什么样"（目标设计），也诚实记录"当前实现乱在哪里"（重建时要避免的坑）。
>
> **当前版本**：`tree-engine 0.1.23`，约 20.8K 行 Python（不含测试）。
>
> **一句话定义**：T.R.E.E.（Textbook Refinement & Enhancement Engine）是一个**资料驱动、以考促写**的自动化教材生成流水线。用户把课件/习题/讲义丢进 `materials/`，引擎自动 OCR → 结构化 → 向量化 → 规划知识图 → 用"出题-盲测-批改-写作"循环生成教材，产出落在 `outputs/`。

---

## 1. 产品目标与核心范式

### 1.1 要解决的问题
把一堆**无结构的原始课程资料**自动转化为**零基础学生可读、逻辑自洽、无知识泄漏**的教材章节，且不依赖任何预定义大纲或外部题库。

### 1.2 核心范式
1. **资料驱动（material-driven）**：没有预定义章节清单，没有外部习题库。能教什么，完全由 `materials/` 里资料经 RAG 后的内容决定。
2. **以考促写（exam-driven writing）**：内容不是一次性生成，而是通过 Examiner 出题 → Student 盲测 → Examiner 批改 → Writer 补写 的迭代逼出来的。只有"零基础学生能凭草稿答对"才算 PASS。
3. **图调度（DAG / BranchRun）**：知识点组织成 `KnowledgeDAG`，切成线性 `KnowledgeBranch`，由确定性 planner 调度，而不是让 LLM 自由决定教学顺序。Examiner 只能在被分配的 branch span 内出题，不能选方向。
4. **忠实性优先（faithfulness）**：PASS 的硬条件是学生的每一步推理都能在**当前草稿或合法先修成品**里找到出处。来自 source material 但不在草稿里的知识 = `Knowledge Bleed`，直接 FAIL。

### 1.3 端到端流水线
```text
materials/                      用户原始资料
  → PaddleOCR-VL-1.6            远程 OCR / 版式解析
  → Archivist 清洗              去噪 + Markdown 规范化
  → Teachable-Unit Cut Plan     切成"可教学单元"边界（sidecar JSON）
  → source RAG (Qdrant)         本地向量化入库
  → Source Inventory            文件内顺序 AI 分组 → KnowledgeGroup
  → KnowledgeNode               跨文件 canonical 合并
  → KnowledgeDAG                依赖图（hard 先修边 + soft 顺序边）
  → KnowledgeBranch             把 DAG 切成线性可执行段
  → BranchRun 调度              确定性 planner 选 ready branch 并发执行
  → Examiner 出题（branch span 内）
  → Student 盲测
  → Examiner 批改（PASS/FAIL）
  → Writer 写/改 declared span 草稿
  → outputs/                    满分通过后入库，并写入 finished RAG
```

---

## 2. 系统分层架构

```text
┌─────────────────────────────────────────────────────────────┐
│ CLI / TUI 层   tree/cli.py（typer）                           │
│   命令: tre / start / watch / status / ingest / rag / doctor  │
│   交互: TREE> slash 命令 + rich 实时 DAG 看板                  │
├─────────────────────────────────────────────────────────────┤
│ 编排层  tree/engine.py  TreeEngine                            │
│   - run(): Step 0→1→2→3→4 主循环                              │
│   - 资料摄入调度 / planner artifact 刷新 / BranchRun 并发      │
├──────────────┬───────────────────────┬──────────────────────┤
│ Agent 层      │ Planner 层（规划）     │ 摄入层（ingest）      │
│ tree/agents/  │ tree/curriculum/ ★旧   │ ingest/ + tree/ingest │
│  examiner     │ tree/planning_v2/ ★新  │  OCR / extractors     │
│  student      │  inventory            │  archivist / cut-plan │
│  writer       │  knowledge_nodes      │                       │
│  archivist    │  planning_graph       │                       │
│               │  branches / ledger    │                       │
├──────────────┴───────────────────────┴──────────────────────┤
│ 基础设施层                                                     │
│   model/client.py     多角色 AsyncOpenAI + 降级               │
│   rag/ (tree) + rag/ (顶层)  Qdrant + 本地 embedding 服务      │
│   state/  Pydantic 状态模型 + StateManager                    │
│   io/  paths / file_ops / git_ops / source_ops               │
│   observability/  progress / logger / retry / limiter         │
│   config.py  分角色配置 + env 加载                            │
└─────────────────────────────────────────────────────────────┘
```

★ **关键结构问题**：见 [§9 当前实现的"乱"](#9-当前实现的乱与重建建议)。`curriculum/`（旧 planner）和 `planning_v2/`（新 planner）**两套并存**，运行时 `tre run` 走旧的，`planning_v2` 只通过 `tre planner rebuild` 暴露，没有接入主循环。

---

## 3. 数据模型与状态文件

### 3.1 工作区目录布局
```text
my-course/                       # 一个独立 workspace = 一门课
├── materials/                   # 用户上传原始资料；子目录名 = source collection
├── outputs/                     # 满分通过的最终教材  <tree_id>/<branch_id>/<NN>.<title>.md
└── .tree/
    ├── config.env               # 可选：仅覆盖本 workspace 的配置
    └── runtime/
        ├── source_materials/    # OCR+Archivist 后的中间 Markdown；embedding 成功后删除
        │   └── <collection>/<file>.md  (+ .teachable-unit-plan.json sidecar)
        ├── drafts/              # Writer 草稿 <tree_id>/<branch_id>/<NN>.<title>.md（不入 RAG）
        ├── rag-store/           # Qdrant 嵌入式向量库
        ├── pipeline-state.json  # BranchExecution / BranchRun 执行状态（权威）
        ├── progress.json        # 看板进度快照
        ├── knowledge-ledger.json    # 已完成 finished outputs 记录（权威）
        ├── source-inventory.json    # KnowledgeGroup（文件内分组）
        ├── knowledge-nodes.json     # KnowledgeNode（跨文件合并后）
        ├── planning-graph.json      # 旧 planner 的图
        ├── knowledge-dag.json       # DAG（节点 + 边）
        ├── knowledge-branches.json  # KnowledgeBranch
        ├── planning/            # planner v2 的 material-manifest + index shards
        ├── pipeline-temp/       # trace.jsonl 等临时文件
        └── services/            # 本 workspace 的服务 pid/log

~/.tree/                         # 用户级全局
├── config.env                  # 默认 API / 模型配置
└── services/                   # 全局 embedding server 的 pid/log（跨 workspace 共享）
```

### 3.2 核心状态模型（`tree/state/models.py`）
```python
PipelineState            # pipeline-state.json 根
├── branch_executions: list[BranchExecutionRecord]
└── branch_runs: list[BranchRunRecord]

BranchExecutionRecord    # 一条可执行 BranchRun 路径
├── execution_path: str          # "<tree_id>/<branch_id>"，tree_id 从 path 第一段派生
├── status: "in_progress"|"completed"
├── outputs_completed: list[str] # 已完成文件序号；下一个 NN = len+1
├── coverage_node_ids: list[str] # 本 branch 要覆盖的 KnowledgeNode
├── current_start_node_id: str|None
├── source_collection(s)         # 来源 collection
└── display_title / provisional_display_title / display_naming_reason

BranchRunRecord
├── branch_id / run_id / status
├── coverage_snapshot: CoverageSnapshot   # 固定快照：可见 ancestor、禁止的 future branch
├── outputs_completed / current_iteration
└── execution_path / tree_id

ExamSections             # Examiner Phase A 输出
├── knowledge_point / covered_node_ids
├── blind_exam / answer_key / writer_instructions

AuditResult              # Examiner Phase B 输出
├── route: Route(PASS | FAIL_KNOWLEDGE_GAP)
├── exam_id / bottleneck_report

WriterResult             # Writer 输出
├── is_exam_too_broad / bloat_description
├── draft_content / draft_path

IterationState           # 单个文件的迭代上下文（内存态）
```

### 3.3 Planner 概念词汇表
| 概念 | 含义 | 落盘文件 |
|---|---|---|
| `KnowledgeGroup` | Inventory 阶段**文件内**顺序 AI 分组 | source-inventory.json |
| `KnowledgeNode` | Candidate 阶段**跨文件** canonical 合并后的最终规划节点 | knowledge-nodes.json |
| `KnowledgeDAG` | KnowledgeNode 构成的教学依赖图 | knowledge-dag.json |
| `KnowledgeBranch` | 从 root/branch node 到下一个 branch/tip 的连续知识段 | knowledge-branches.json |
| `BranchRun` | 一个 active branch 的独立执行循环 | pipeline-state.json |
| `FinishedNode` | 已通过考试并入库的输出节点 | knowledge-ledger.json |

---

## 4. 摄入流水线（Ingest）

入口：`tree/engine.py::ingest()` → `tree/ingest.py::ingest_path()`。底层提取器在 `ingest/`。

### 4.1 文件类型与提取
`MATERIAL_EXTENSIONS = {.pdf .ppt .pptx .docx .md .txt .png .jpg .jpeg .bmp .tif .tiff .webp}`

`ingest/pipeline.py::detect_type()` 路由到提取器（`ingest/extractors/`）：
- **pdf** → `pdf_extractor`：全部走 PaddleOCR（即便有内嵌文本，也为公式精度做 OCR）。**>99 页**自动切成 ≤99 页临时 PDF，OCR 后按序拼回单一 Markdown。
- **image** → `image_extractor` → PaddleOCR。
- **docx** → `docx_extractor`（python-docx）。
- **presentation (ppt/pptx)** → `presentation_extractor`：python-pptx 提取文本/表格/备注 + 内嵌图片 OCR；旧 ppt 纯文本兜底。**建议用户手动转 PDF 再上传**以获得更好版式/公式识别。
- **text (txt/md)** → 直接读。

### 4.2 OCR 引擎（`ingest/ocr_engine.py::OCREngine`）
- 远程 PaddleOCR-VL-1.6，job URL/token 固定 + 用户 token。
- `optionalPayload = {useDocOrientationClassify, useDocUnwarping, useChartRecognition, visualize=False}`。
- 上传默认每 5s 一个文件（`SOURCE_OCR_UPLOAD_INTERVAL_SEC`），上传+轮询可并发（`SOURCE_OCR_CONCURRENCY`）。
- `clean_ocr_markdown_text()`：本地清除图片链接、`markdownImages` 残留，不进入后续 chunker。

### 4.3 Archivist 清洗（`tree/agents/archivist.py` + `ingest/archivist.py`）
**职责刻意很窄**（见 `ARCHIVIST_PROMPT`）：
1. 去非教学噪声（版权页、页眉页脚、页码、水印、目录噪声、图片残链）。
2. 规范标题层级（`#/##/###`），保持原教学顺序。
3. 删所有图片链接/占位，不写图片描述。
- **严禁**摘要、改写、扩写、拆分/合并知识点、提取概念/先修、重排。不确定就保留。

### 4.4 Teachable-Unit Cut Plan（`tree/ingest.py::_build_cut_plan`）
Archivist 第二个任务：把清洗后的 Markdown 切成**可教学单元（teachable unit）**边界。
- 输出严格 JSON：`units[]`（含 `start_line/end_line/unit_title/heading_path/unit_kind/include_in_rag/teaching_role/core_concepts/prerequisites/unit_summary/boundary_reason`）+ `skipped_ranges[]`。
- **每一行**都必须被某个 unit 或 skipped_range 覆盖，不能静默丢行。
- 大文件用 `_build_segmented_cut_plan` 分窗口处理；有 `_deterministic_heading_cut_plan` 作确定性兜底。
- 切分原则：偏好少而宽的单元，不按公式/性质/图表/例题/习题拆同一概念。各学科有专门启发式（数学/物理/化学/CS/历史/语言）。
- 落盘 sidecar：`<file>.md` 旁的 `<file>.teachable-unit-plan.json`（`_write_cut_plan_sidecar`）。
- 校验在 `rag/chunker.py::validate_cut_plan` + `_reject_overfragmented_cut_plan`，repair 次数 `SOURCE_ARCHIVIST_CUT_PLAN_REPAIR_ATTEMPTS`。

### 4.5 RAG 入库
`tree/engine.py::_prepare_source_markdown_for_loop()` 编排：
- 用 `source-manifest`（文件指纹）做增量：只处理新增/变更资料。
- ingest 并发 `SOURCE_INGEST_CONCURRENCY`，embedding 串行 `SOURCE_EMBEDDING_CONCURRENCY=1`。
- 第一个 source markdown 产出后即可开始串行 embedding（流水线化）。
- **source markdown embedding 成功后从磁盘删除**（节省空间，RAG 是唯一真相）。
- `_ensure_all_source_markdown_embedded()`：进入主循环前阻塞直到全部入库；indexer 不可用则报错。

---

## 5. RAG 设计

### 5.1 组件
- **向量库**：Qdrant 嵌入式（`path=.tree/runtime/rag-store`），单 collection `tree-knowledge`，COSINE，维度 2560（Qwen3-Embedding-4B-Q8_0 全维）。
- **Embedding 服务**：本地 `llama-cpp-python` + FastAPI（`rag/server.py` + `rag/embed.py`），OpenAI 兼容 `/v1/embeddings`，端口 8788。模型 `Qwen3-Embedding-4B-Q8_0.gguf`（~4.3GB，首启自动下载）。**全局共享**（pid/log 在 `~/.tree/services`），跨 workspace 不重复下载。
- **客户端**：`tree/rag/client.py::RAGClient`（index/query/scroll/delete），`tree/rag/indexer.py` 适配器。

### 5.2 chunk 策略（`rag/chunker.py::chunk_markdown`）
- 优先用 cut-plan 的 teachable units 作 chunk 边界（`boundary_source=archivist_cut_plan`）；否则按标题/大小兜底。
- 约 1500–3000 token 语义块，查询命中后扩展相邻块（`neighbor_window`）。
- 每 chunk payload 富元数据：`section_id/chunk_type/concepts/formulas/formula_signatures/heading_path/unit_title/teaching_role/core_concepts/prerequisites/unit_summary/line_range/content_kind/source_collection/...`。
- chunk 预算：`{def:2000, proof:3000, example:2400, narrative:1500}`。
- `content_kind` 三个命名空间：`source` / `finished` / `draft`（draft 不入库）。

### 5.3 RAG 使用规则（教学边界的核心）
- **source RAG** = 教师侧 ground truth：决定该教什么、answer key 该写什么。**不能**作为 student faithfulness 证据。
- **finished-output RAG** = 学生可见的已学知识 + 去重边界；仅当在 BranchRun prior scope 内才可见。
- **draft** 不入 RAG，Student 直接读当前 draft 全文。
- **Learned RAG Hit** = 已通过成品教材摘录（不是 source material），Student 只能引用其明确支持的步骤。

---

## 6. Planner 设计（规划层）

> ⚠️ **这是整个系统最复杂、也最"乱"的部分。** 下面先讲**运行时实际用的旧 planner（`curriculum/`）**，再讲**目标干净架构（`planning_v2/`）**。重建时应以 v2 思路为蓝本，见 [§9](#9-当前实现的乱与重建建议)。

### 6.1 运行时 planner 流水线（`curriculum/`，engine 实际调用）
六阶段（看板里就是这 6 步 planner 进度）：
1. **Source Inventory**（`inventory.py::rebuild_source_inventory_with_ai`，1518 行）：从 source RAG 把 chunk 按文件内顺序 AI 分组成 `KnowledgeGroup`，并发 `SOURCE_INVENTORY_FILE_CONCURRENCY`。提取 core_concepts/prerequisites/teaching_role，合并辅助组/噪声组。
2. **KnowledgeNodes**（`knowledge_nodes.py::rebuild_knowledge_nodes_with_ai`，**2204 行**）：跨文件把 group 合并成 canonical `KnowledgeNode`。算 pairwise group 相似度 → union-find 聚类 → AI merge review（按批，超时 `KNOWLEDGE_NODE_MERGE_TIMEOUT_SEC`）→ 选 canonical 标题。
3. **Merge Review**：AI 审核高风险合并（duplicate/merge_needed），有确定性 fallback 决策。
4. **KnowledgeDAG**（`branches.py::build_knowledge_dag` + `planning_graph.py`，**1992 行**）：节点关系打分（concept/chunk/source/prerequisite overlap）→ 关系边（prerequisite/adjacent/duplicate/...）→ source 顺序回填 → 断环 → 加权传递归约。增量森林 planner：选 root、finished output 变真实节点、新 branch 挂到 finished、remaining 太远就重选 root。
5. **KnowledgeBranches**（`branches.py::build_knowledge_branches`）：把 DAG 切成线性 branch，算 coverage_snapshot、branch 依赖、排序。
6. **Schedule BranchRuns**（`branches.py::start_ready_branch_runs`）：选 ready branch（依赖已满足）写入 pipeline-state，受 `MAX_ACTIVE_BRANCH_RUNS=2` 限制。

支撑：`ledger.py`（finished 记录、去重 brief）、`branch_execution_naming.py`（AI 命名 tree/branch，有 fallback）。

### 6.2 目标干净 planner（`planning_v2/`，目前仅 `tre planner rebuild`）
这是后来按 refactor 文档重写的**确定性、可审计、分阶段**架构，**结构远好于旧版**，应作为重建蓝本：

```text
planning_v2/
├── orchestration/
│   ├── pipeline.py       rebuild_planner(root)：串联 5 个 stage，每步包 envelope 落盘
│   └── scheduler.py      branch 调度
├── stages/
│   ├── material_scan.py     扫 materials → material-manifest（active/inactive 指纹）
│   ├── source_index.py      build_group_shard：material → KnowledgeGroup shard
│   ├── node_canonicalize.py canonicalize_groups：group → KnowledgeNode（确定性）
│   ├── dependency_build.py  build_dependency_graph：hard 先修边 + soft 顺序边
│   └── branch_build.py      build_branches：hard DAG 切成静态 branch
├── validators/source_structure_validator.py  结构校验 + normalize
├── storage/artifact_store.py  envelope() / artifact_hash() / write_json_atomic()
├── ai/
│   ├── adapters.py      AI 适配器
│   └── prompts/*.md     cut_plan_repair / node_merge_review / dependency_review / source_structure
├── schemas/diagnostics.py  统一 diagnostic 结构（severity/stage/entity/reason_code）
└── ids.py              prefixed_id() 稳定 ID + normalize_text_key/normalize_concepts
```

**v2 关键设计优点（重建必须保留）**：
- **Artifact envelope**：每个产物带 `{schema, inputs[{path,hash}], diagnostics, data, algorithm_versions}`，可追溯、可增量、可缓存（靠 `artifact_hash` 比对输入是否变化）。
- **确定性优先，AI 仅审边界**：`canonicalize_groups`/`build_dependency_graph`/`build_branches` 全是纯函数确定性算法；AI 只在低置信度边界（cut plan 修复、node merge、dependency direction）介入。
- **稳定 ID**：`prefixed_id("kn", group_ids)` 由内容派生，幂等。
- **Shard 化**：每个 material 一个 group shard（`planning/index/materials/<mat_id>/knowledge-groups.json`），单文件变更只重建该 shard。
- **小而专的 stage 文件**：每个 stage 100–320 行，对比旧版单文件 2000+ 行。

**dependency_build 算法要点**：
- 遍历节点的 `prerequisites`，文本亲和度匹配（`_text_affinity`：完全/包含/n-gram dice/字符 overlap）找最佳 from_node，要求源在前（`source_order_index`）。
- 通用先修（`_GENERIC_PREREQUISITES`，如"三角函数""牛顿第二定律"）跳过不建边。
- 跨 collection 弱先修（`_CROSS_COLLECTION_WEAK_PREREQUISITES`）降级为 soft order 边而非 hard。
- exercise_derived 节点只建 soft 关联边。
- hard 边做 `_transitive_reduction`；soft 边回填 collection 内 source 顺序。

**branch_build 算法要点**：branch 起点 = 入度 0 或入度 >1（merge 点）或 branch 点的子节点；从起点沿"出度=1 且下游入度=1"链走到底；coverage 处理 merge 点归属；linkage 算 upstream/downstream branch。

---

## 7. Agent 层与执行循环

### 7.1 四个角色（`tree/agents/`，prompt 在 `prompts.py`）
| Role | 文件 | 职责 |
|---|---|---|
| **Examiner** | `examiner.py` | Phase A 在 ActiveBranch 内出题（声明连续 `Covered_Node_IDs`）；Phase B 双重审计（正确性 + 忠实性）出 Bottleneck Report，给 PASS/FAIL。**不能**选 root/branch/章节、不能发完成信号。 |
| **Student** | `student.py` | 零基础学习者，只用当前 draft + 合法 prior finished + Learned RAG Hit 作答，证据驱动逐步推理，缺证据就报 logic gap。 |
| **Writer** | `writer.py` | CREATE/OPTIMIZE 模式，按 Bottleneck Report + declared branch span 写/改教材 Markdown。OPTIMIZE 时只补最小缺陷集。严格 LaTeX 渲染契约。 |
| **Archivist** | `archivist.py` | OCR 输出轻量清洗 + teachable-unit cut plan（见 §4.3/4.4）。 |

prompt 加载：`AgentLoader`（内置 prompt，`get_prompt(name)`）。

### 7.2 LLM 客户端（`tree/model/client.py`）
- 每角色独立 `AsyncOpenAI`（可分角色配 key/url/model）。
- OpenAI 兼容 Chat Completions，任何兼容供应商可用。
- Examiner 有降级逻辑：连续失败 `PRO_DEGRADATION_THRESHOLD` 次后冷却期内降级到 student 模型（`DegradationTracker`）。
- 重试：`retry_with_backoff`，`MAX_RETRIES`，格式重试 `MAX_FORMAT_RETRIES`。

### 7.3 主循环（`tree/engine.py::run` → `process_branch_execution` → `_iteration_loop`）

**run() 顶层循环**：
```text
reconcile finished outputs → 准备 source markdown（OCR/archivist/embed）
loop:
  load state → 激活 ready BranchRun
  若无 in_progress：刷新 planner artifacts → 调度 ready branch
    若仍无 → 检查 blockage（blocked）或全覆盖（WOODS_COMPLETE）退出
  并发执行 in_progress[:MAX_ACTIVE_BRANCH_RUNS] 个 process_branch_execution
```

**单 BranchRun 的 Step 0→4**：
- **Step 0**：从 pipeline-state 找 `in_progress` branch，读 execution_path/coverage_node_ids，下一个 NN = `len(outputs_completed)+1`。
- **Step 1（Examiner Phase A）**：给 ActiveBranch Context + source RAG + 合法 prior scope + NN，从 branch 第一个未覆盖 node 起出题，输出 `[Next_Knowledge_Point] [Covered_Node_IDs] [Blind_Exam] [Answer_Key] [Writer_Instructions]`。
- **Step 2（Student）**：只给当前 draft + prior scope finished + snapshot-filtered Learned RAG Hit + blind exam，盲测作答。
- **Step 3（Examiner Phase B）**：审计正确性 + 忠实性 + 知识缺陷，出 Bottleneck Report + `ROUTE: PASS|FAIL_KNOWLEDGE_GAP`。
- **Step 4（Writer）**：FAIL 时按 Bottleneck Report 写/改 draft → `drafts/<tree_id>/<branch_id>/<NN>.<title>.md`，回到 Step 2。
- **PASS**：draft 移到 `outputs/...`，更新 ledger + finished RAG + branch coverage，标记 active node 完成。
- 迭代上限 `MAX_ITERATIONS`（`IterationLimiter`）。

### 7.4 Prior Scope（可见性边界，关键正确性约束）
Examiner/Writer/Student 能看到的 prior finished outputs **只来自**：
1. 当前命题起点 KnowledgeNode 的 **DAG ancestor closure** 对应的 finished outputs。
2. 当前 branch 内、本轮 span 起点**之前**已完成的文件。

并行 branch 新完成的 outputs **不进入**当前 BranchRun 的固定 snapshot（`CoverageSnapshot` 冻结）。这保证盲测的确定性与无泄漏。

### 7.5 运行状态机
连续运行：文件 PASS → 继续当前 branch；branch 完成 → 解锁下游 branch；无 ready branch 且有 diagnostic → `blocked`；全部 source node 覆盖 → `WOODS_COMPLETE`。

---

## 8. CLI / UX 与可观测性

### 8.1 命令（`tree/cli.py`，2910 行 — 太大，见 §9）
- 生命周期：`tre`（交互）/`start`/`continue`/`stop`/`quit`/`run`/`resume`。
- 诊断：`doctor`（查 Python/PATH/包/TREE_HOME/config/workspace/embedding/git，只读不改）/`status`/`progress`/`watch`/`logs`/`materials`/`models`/`clean`/`prompts`。
- 配置：`setup`(`--force`/`--workspace`)。
- 摄入：`ingest --input <path> --collection <name> [--no-archivist] [--no-index]`。
- RAG 子命令：`rag status|ledger|inventory|nodes|graph|search`。
- planner v2 子命令：`planner rebuild`、`material retry|skip`、`branchrun retry`。
- 交互内 slash：`/start /watch /progress /status /stop /quit /exit /help`。

### 8.2 交互与守护进程
- 进入 `TREE>` 后用 slash 命令；`/start` 后台跑引擎并确保 embedding server。
- **强制关闭**（Ctrl+C/终端关闭/输入断流）→ 自动 `/quit`（停引擎 + embedding）；只有 `/exit` 才保留后台服务。
- 服务管理在 `tree/services.py`（pid/log/stop 文件，`stop_requested()` 配合 `_raise_if_stop_requested()` 安全检查点）。

### 8.3 看板（rich 实时渲染，`cli.py` 下半部 + `observability/progress.py`）
- 第一个 BranchRun 前：显示 OCR / source embedding / Planner 6 阶段准备进度。
- BranchRun 后：切到"项目学习图"——编号 DAG，已完成 node 绿、未完成白、已完成 branch 棕、运行中 branch 浅棕带 `▶`。
- 底部固定两个 BranchRun 循环槽位（`MAX_ACTIVE_BRANCH_RUNS=2`），空闲显示 idle。
- progress.json 三块：source_ingest / learning_loop / planner_progress，`_deep_update` 原子合并。

### 8.4 配置体系（`tree/config.py`）
- 加载顺序：`~/.tree/config.env`（全局）→ `.env`（legacy workspace）→ `.tree/config.env`（workspace 覆盖）。空值不覆盖。
- 分角色：`EXAMINER_/STUDENT_/WRITER_/ARCHIVIST_` 各自 `_API_KEY/_BASE_URL/_MODEL`，回退到 `LLM_*` 默认。
- `Settings` 是 frozen dataclass，`from_env()` 构造，含全部并发/超时/planner attempts 旋钮（见 README 配置段完整列表）。

---

## 9. 当前实现的"乱"与重建建议

> 这一节是本文件存在的真正理由。诚实记录技术债，重建时**对症避免**。

### 9.1 主要问题
1. **两套 planner 并存且割裂**：`curriculum/`（旧，运行时在用，~7000 行）与 `planning_v2/`（新，干净，但只挂在 `tre planner rebuild`，没接入 `engine.run()`）。同样的 KnowledgeNode/DAG/Branch 概念有两份不一致实现，写同名 json 文件却 schema 不同（v2 带 envelope，旧版裸 dict）。**这是最大的混乱源。**
2. **巨型文件**：`cli.py` 2910 行、`knowledge_nodes.py` 2204、`planning_graph.py` 1992、`engine.py` 1793、`inventory.py` 1518、`branches.py` 1383。单文件几十个私有 `_helper`，可读性/可测性差。
3. **CLI 职责过载**：`cli.py` 同时塞了命令定义、看板渲染（`render_dag_ascii`/`_branch_run_slot_panel` 等十几个渲染函数）、DAG 拓扑计算。渲染应独立成模块。
4. **engine.py 既编排又塞满纯函数**：1266 行后全是 module-level 私有函数（manifest、fingerprint、context 拼装），应下沉到 io/服务层。
5. **命名历史包袱**：磁盘文件还兼容旧名（`candidate-nodes.json` vs `knowledge-nodes.json`），概念演进留痕（curriculum-map → candidate → knowledge node）。
6. **docs/ 里有多份"已被取代"的 refactor 计划**（deterministic→incremental forest），真实意图分散在 4 个 superseded 文档 + 2 个 superpowers plan 里。
7. **`.DS_Store` 入库**、`project-workflow 2.html`（带空格副本）等杂物。

### 9.2 重建目标架构建议
**以 `planning_v2` 的设计哲学统一全系统**：

```text
tree_engine/tree/
├── cli/                      # 拆分巨型 cli.py
│   ├── commands.py           # 仅 typer 命令定义
│   ├── dashboard/            # 看板渲染（render_dag / slots / panels）
│   └── interactive.py        # TREE> REPL
├── engine/
│   ├── orchestrator.py       # run() 主循环（薄）
│   ├── branch_run.py         # Step 0→4 单 BranchRun
│   └── ingest_driver.py      # 资料摄入编排（从 engine.py 抽出）
├── agents/                   # 保持现状（已较清晰）examiner/student/writer/archivist + prompts
├── planner/                  # 统一 = 现 planning_v2 提升为唯一 planner
│   ├── orchestration/pipeline.py
│   ├── stages/               # material_scan / source_index / node_canonicalize /
│   │                         #   dependency_build / branch_build / branch_schedule
│   ├── validators/ storage/ ai/ schemas/ ids.py
├── ingest/                   # OCR / extractors / archivist / cut_plan
├── rag/                      # client / chunker / embed / server
├── model/                    # LLM client
├── state/  io/  observability/  config.py
```

**统一原则**：
1. **删掉 `curriculum/`**，把 `engine.run()` 接到 `planning_v2` 的 `rebuild_planner()` + scheduler。运行时和 `tre planner rebuild` 走同一条路。
2. **全产物 envelope 化**（schema + input hash + diagnostics），增量重建靠 hash 比对。
3. **确定性算法 + AI 仅审边界**：保持 v2 的纯函数 stage，AI 只做 cut-plan repair / node merge review / dependency direction review。
4. **单文件 < 400 行**为软约束；超了就按职责拆 stage/helper 模块。
5. **看板/渲染与命令分离**，engine 不持有纯函数工具。
6. **状态文件单一 schema**，不再兼容旧名。
7. **一份权威设计文档**（即本文件）替代散落的 superseded refactor 文档。

### 9.3 必须保留的核心资产（重建别丢）
- **四个 agent 的 prompt**（`prompts.py`）——这是产品的灵魂，凝结了 faithfulness/anti-duplication/branch-span 等大量经验规则。**几乎可原样迁移。**
- **Prior Scope + CoverageSnapshot 可见性模型**（§7.4）——正确性的关键。
- **teachable-unit cut plan + boundary-aware chunking**（§4.4/5.2）。
- **planning_v2 的 stage 算法**（canonicalize/dependency/branch）。
- **RAG 三命名空间 + 使用规则**（§5.3）。
- **多角色 LLM + Examiner 降级**（§7.2）。
- **全局共享 embedding server + 增量 source manifest**。

---

## 10. 技术栈与依赖

- **Python** >= 3.12，typer CLI，pydantic 状态，structlog/rich 可观测。
- **LLM**：openai SDK（OpenAI 兼容 Chat Completions），httpx。
- **RAG（optional `[rag]`）**：llama-cpp-python（本地 embedding）、huggingface-hub、qdrant-client、fastapi、uvicorn。
- **文档提取**：python-docx、python-pptx、pypdf；OCR 走远程 PaddleOCR-VL-1.6。
- **打包**：`tree-engine`，entry points `tre` / `tree-run` → `tree.cli:app`；`pipx install "tree-engine[rag] @ git+..."` 分发。
- **dev**：pytest(-asyncio/-mock)、ruff、mypy(strict)。
- **验证基线**：`python -m pytest && ruff check tree_engine tests && python -m compileall tree_engine/tree tree_engine/rag tree_engine/ingest`。

---

## 11. 重建路线建议（增量、每步可运行）

1. **骨架 + 配置 + io/paths + state 模型**（直接迁移，已干净）。
2. **ingest 层**（OCR/extractors/archivist/cut-plan）+ **RAG 层**（client/chunker/embed/server）——基本可原样迁移。
3. **planner（以 v2 为唯一实现）**：material_scan → source_index → node_canonicalize → dependency_build → branch_build → schedule，全部 envelope 化，配 stage 级单测。
4. **agents + prompts**（迁移）+ **model client**（迁移）。
5. **engine 编排**：薄 orchestrator + branch_run（Step 0→4）+ ingest_driver，接 planner。
6. **CLI + 看板**（命令/渲染/REPL 三分）。
7. **doctor / 增量 manifest / 服务管理**。
8. 端到端：真实资料放 `materials/` → `tre start` → `tre watch` 验收。

每步跑验证基线，保持可运行。

---

*本文件由对现有 `tree-engine 0.1.23` 代码库的系统性审阅生成，作为新工作区重建的唯一权威蓝本。*
