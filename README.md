# TREE

**T.R.E.E.**（Textbook Refinement & Enhancement Engine）是一个资料驱动、以考促写的自动化教材生成引擎。它从课程资料出发，先把 PDF、图片、Word、PPT、Markdown 等材料整理成可验证的知识 DAG，再按 DAG 中的单个 KnowledgeNode 运行 Examiner / Student / Writer 循环，最终把通过盲测的教材 Markdown 写入 `outputs/`。

TREE 的核心目标不是简单总结资料，而是生成能够被“零基础学生”盲测通过的教材内容。LLM 负责语义判断和写作，程序负责契约校验、行号覆盖、DAG 构建、RAG 检索边界、NodeRun 调度和状态持久化。

## 目录

- [当前能力](#当前能力)
- [整体流程](#整体流程)
- [安装](#安装)
- [初始化与配置](#初始化与配置)
- [准备资料](#准备资料)
- [运行 TREE](#运行-tree)
- [查看进度与产物](#查看进度与产物)
- [Embedding 服务](#embedding-服务)
- [常用命令](#常用命令)
- [工作区结构](#工作区结构)
- [关键契约](#关键契约)
- [开发与测试](#开发与测试)
- [故障排查](#故障排查)

## 当前能力

- 多格式资料摄入：PDF / 图片走 PaddleOCR-VL，DOCX / PPTX 先结构化抽取并对内嵌图片 OCR，Markdown / TXT 直接读取。
- OCR checkpoint：原始 OCR Markdown 会保存到 `.tree/runtime/ocr/`，便于检查和从中间阶段重试。
- Archivist 清洗和切分：先清理 OCR 噪声，再把 cleaned Markdown 切成 MTU（Minimal Teachable Unit，最小可教学单元）。
- 严格 MTU 校验：MTU 必须连续覆盖全文行号，不允许 gap、overlap、`skipped_ranges`；最终 concept MTU 必须有 `defines`。
- Source RAG 前置索引：MTU 生成后立即写入 Qdrant，本地 Qwen3 embedding 结果会被复用给后续聚类。
- Dagger 构图：先用 embedding / shared defines 生成候选 cluster，再由 Dagger 确认 KnowledgeNode；之后选择 `required_defines`，程序确定性生成 prerequisite DAG。
- 自动 DAG SVG：生成 `knowledge-dag.json` 后会自动生成 `.tree/runtime/planner/knowledge-dag.svg`，节点主体显示 `NNN. 知识点标题`，方便和后续 output 文件对应。
- NodeRun 运行层：取消 branch 切割，Examiner 每次只为 1 个 KnowledgeNode 出题，最多 5 个 active node 并行。
- RAG 边界控制：Student 只读取当前草稿和已完成先修 node 的 finished-output RAG 命中片段，不能直接读取 source 原文或未来/旁支输出。
- 进度面板：`tre watch` 展示 OCR / Clean / Cut / Embed / Cluster / Link / NodeRun 七个阶段的进度条。

## 整体流程

```text
materials/
  -> extractors / PaddleOCR：读取原始资料并保存 OCR Markdown checkpoint
  -> Archivist clean：清理图片、表格、页眉页脚等非教学噪声
  -> Archivist cut_mtus：按行号切成 MTU，并进行 coverage / short-unit / metadata repair
  -> Source RAG index：把 MTU 文本写入 Qdrant，同时生成本地 embedding
  -> Cluster：用 embedding 相似度和 shared defines 生成候选 cluster，交给 Dagger 确认 node
  -> Link：Dagger 为每个 node 选择 required_defines，程序映射成 prerequisite edges
  -> knowledge-dag.json / knowledge-dag.svg
  -> NodeRun scheduler：只调度所有 prerequisite parent 已完成的 ready node
  -> Examiner：为当前单个 node 命题并给 Writer 指令
  -> Student：只基于当前草稿和先修 finished-output RAG 作答
  -> Examiner：批改并决定 PASS / FAIL
  -> Writer：创建或修补当前 node 教材草稿
  -> PASS：写入 outputs/，更新 knowledge-ledger，并索引 finished RAG
```

## 安装

TREE 要求 Python `>=3.12`。如果只是本地源码开发，推荐直接在当前 checkout 中安装；如果要像普通用户一样在任意课程文件夹运行，则可以用 `pipx` 安装包。

### 方式一：从源码 checkout 运行

macOS / Linux：

```bash
git clone <TREE_REPOSITORY_URL> Tree
cd Tree
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[rag,dev]"
tre doctor
```

Windows PowerShell：

```powershell
git clone <TREE_REPOSITORY_URL> Tree
cd Tree
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[rag,dev]"
tre doctor
```

如果只想跑不依赖 RAG 的单元开发，可以先安装：

```bash
pip install -e ".[dev]"
```

完整端到端运行需要 `[rag]`，因为 TREE 会使用 Qdrant 和本地 embedding server。

### 方式二：用 pipx 安装

macOS 推荐先安装 `pipx`：

```bash
python3.12 --version
git --version
brew install pipx
pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

Linux：

```bash
python3.12 --version
git --version
python3.12 -m pip install --user pipx
python3.12 -m pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

Windows PowerShell：

```powershell
py -3.12 --version
git --version
py -3.12 -m pip install --user pipx
py -3.12 -m pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

如果 `pipx ensurepath` 修改了 PATH，请重新打开终端。安装后在任意课程文件夹运行：

```bash
tre
```

即可进入 `TREE>` 交互界面。

### 更新

如果使用 `pipx` 安装：

```bash
tre quit
pipx upgrade tree-engine
tre doctor
```

如果需要强制从 GitHub 重新安装：

```bash
pipx uninstall tree-engine
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

更新不会删除课程工作区中的 `materials/`、`outputs/` 和 `.tree/`。

## 初始化与配置

在一个课程目录中初始化工作区：

```bash
mkdir my-course
cd my-course
tre init
```

`tre init` 会创建：

```text
materials/
outputs/
.tree/
```

运行交互式配置向导：

```bash
tre setup
```

默认会写入全局配置 `~/.tree/config.env`，所有 TREE workspace 都会复用这份配置。向导会依次引导输入：

- Shared LLM / agent API key
- LLM base URL
- Default LLM model
- Examiner / Student / Writer / Archivist / Dagger 五个角色模型
- PaddleOCR API key

如果只想为当前课程写覆盖配置：

```bash
tre setup --workspace
```

如果配置文件已存在，`tre setup` 会提示使用 `--force` 重新进入向导：

```bash
tre setup --force
tre setup --workspace --force
```

脚本或 CI 中也可以直接传入参数，非交互写入目标配置。默认仍写全局配置；加 `--workspace` 写当前工作区：

```bash
tre setup \
  --llm-api-key "$LLM_API_KEY" \
  --llm-base-url "https://api.deepseek.com" \
  --llm-model "deepseek-v4-flash" \
  --paddleocr-api-token "$PADDLEOCR_API_TOKEN" \
  --paddleocr-api-url "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
```

也可以手动编辑 `~/.tree/config.env` 或 `.tree/config.env`。配置加载顺序为：

```text
~/.tree/config.env -> ./.env -> ./.tree/config.env
```

后加载的文件会覆盖先加载的文件；空值不会覆盖已有值。常见做法是把默认 API 配置放在 `~/.tree/config.env`，再用 `.tree/config.env` 覆盖当前课程。

### 最小配置模板

```bash
# Shared OpenAI-compatible Chat Completions provider
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash

# PaddleOCR
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_API_TOKEN=
PADDLEOCR_MODEL=PaddleOCR-VL-1.6
```

### 角色模型覆盖

TREE 有五个 LLM 角色：

```text
examiner   出题、批改、判断 PASS/FAIL
student    零基础学生，只基于允许的资料答题
writer     根据瓶颈报告写作或修补教材
archivist  清洗 OCR Markdown、切 MTU、局部 repair
dagger     聚类 MTU、命名 node、选择 required_defines、修复 DAG 冲突
```

默认使用 `LLM_*`；也可以为某个角色单独覆盖：

```bash
EXAMINER_API_KEY=
EXAMINER_BASE_URL=
EXAMINER_MODEL=

STUDENT_API_KEY=
STUDENT_BASE_URL=
STUDENT_MODEL=

WRITER_API_KEY=
WRITER_BASE_URL=
WRITER_MODEL=

ARCHIVIST_API_KEY=
ARCHIVIST_BASE_URL=
ARCHIVIST_MODEL=

DAGGER_API_KEY=
DAGGER_BASE_URL=
DAGGER_MODEL=
```

查看当前角色模型配置：

```bash
tre models
```

`tre models` 不会要求输入内容，只会读取配置并打印每个角色的 `model @ base_url`。

### 常用运行参数

```bash
# Agent / LLM
MAX_ITERATIONS=5
MAX_RETRIES=3
MAX_FORMAT_RETRIES=2
LLM_TIMEOUT_SEC=480

# Source ingest / OCR
SOURCE_INGEST_CONCURRENCY=16
SOURCE_OCR_CONCURRENCY=5
SOURCE_OCR_PDF_MAX_PAGES_PER_JOB=99
SOURCE_OCR_UPLOAD_INTERVAL_SEC=5
SOURCE_EMBEDDING_CONCURRENCY=1

# Archivist
ARCHIVIST_MTU_CUT_TIMEOUT_SEC=480
ARCHIVIST_MTU_REPAIR_ATTEMPTS=8

# Dagger
DAGGER_BUILD_TIMEOUT_SEC=480
DAGGER_REPAIR_ATTEMPTS=3
DAGGER_MAX_NODES_PER_CALL=400
DAGGER_EMBED_CLUSTER_ENABLED=true
DAGGER_CLUSTER_SIMILARITY_THRESHOLD=0.80
DAGGER_CLUSTER_TOP_K=5
DAGGER_CLUSTER_MAX_SIZE=8
DAGGER_CLUSTER_AUTO_ACCEPT_SINGLETON=true
DAGGER_CLUSTER_AUTO_ACCEPT_SAME_COLLECTION=false

# NodeRun
MAX_ACTIVE_NODE_RUNS=5
MAX_EXAMINER_SPAN_NODES=3
```

## 准备资料

把资料放入 `materials/`。子目录名会作为 collection 名称：

```text
materials/
├── 课件/
│   ├── 5. 化学平衡通论.pdf
│   └── 6. 化学动力学简介.pdf
└── 习题/
    ├── 普通化学A-作业01.pdf
    └── 普通化学A-作业02.docx
```

支持的资料类型包括：

```text
PDF
PNG / JPG / JPEG / WEBP / BMP / TIFF
DOCX
PPT / PPTX
Markdown / TXT
```

PDF 和图片会调用 PaddleOCR。DOCX / PPTX 会先抽取文本、表格、备注和内嵌图片，必要时再对图片 OCR。为了获得更好的版式、公式和图表识别效果，复杂 PPT/PPTX 建议先手动导出为 PDF。

也可以用命令把外部资料复制到 `materials/`：

```bash
tre ingest --input /path/to/file.pdf --collection 课件
tre ingest --input /path/to/folder --collection 课件
```

## 运行 TREE

### 交互式运行

在课程工作区运行：

```bash
tre
```

进入 `TREE>` 后常用 slash commands：

```text
/start      后台启动 TREE engine
/watch      显示一次全流程进度面板
/progress   打印 progress.json
/status     查看当前 workspace 状态
/materials  列出 materials/ 下支持的资料
/stop       停止后台 engine
/quit       停止后台 engine 并离开 shell
/exit       只离开 shell，不停止后台服务
/help       查看交互命令
```

### 前台运行

```bash
tre run
```

`tre run` 会在当前终端中执行完整 pipeline，适合调试或观察日志。

恢复或继续运行：

```bash
tre resume
tre continue
```

### 后台运行

```bash
tre start
tre watch
tre status
tre stop
```

当前后台生命周期只管理 workspace engine。Embedding server 需要按下文单独启动，或在你的部署脚本中作为独立服务管理。

### 只重建 Planner

如果只想从资料生成 MTU、KnowledgeNode、DAG 和 SVG，而不进入 NodeRun 写作：

```bash
tre planner rebuild
```

基于已有 `knowledge-dag.json` 重新画 SVG：

```bash
tre planner dag-svg
```

## 查看进度与产物

### `tre watch`

`tre watch` 是一屏式进度面板，显示七个阶段：

```text
TREE Watch
phase: running
message: ...
materials: 6
nodes: 74
edges: 81

Progress
OCR      [##################]     6/6 complete
Clean    [##################]     6/6 complete
Cut      [##################]     6/6 complete
Embed    [########----------]   34/74 running   当前: ...
Cluster  [------------------]     0/0 pending
Link     [------------------]     0/0 pending
NodeRun  [##----------------]    4/74 running   当前: kn_xxx, kn_yyy
```

七个阶段含义：

- `OCR`：原始资料抽取 / OCR 完成数量。
- `Clean`：Archivist clean chunk 完成数量。
- `Cut`：Archivist cut_mtus chunk 完成数量。
- `Embed`：source MTU 写入 Qdrant / node_id 回填进度。
- `Cluster`：Dagger cluster refinement 进度。
- `Link`：Dagger prerequisites 与 deterministic edge construction 进度。
- `NodeRun`：已 PASS 的 KnowledgeNode 数量和 active node。

### Planner artifacts

Planner 产物位于：

```text
.tree/runtime/planner/material-manifest.json
.tree/runtime/planner/mtus.json
.tree/runtime/planner/knowledge-nodes.json
.tree/runtime/planner/knowledge-dag.json
.tree/runtime/planner/knowledge-dag.svg
```

其中：

- `material-manifest.json`：资料扫描结果和增量缓存依据。
- `mtus.json`：Archivist 切出的 MTU。
- `knowledge-nodes.json`：Dagger 确认后的 canonical KnowledgeNode。
- `knowledge-dag.json`：nodes / prerequisite edges / roots。
- `knowledge-dag.svg`：可视化知识图谱，节点名称与 output 编号对齐。

### Outputs

NodeRun PASS 后，最终教材会平铺写入：

```text
outputs/
├── 001.氧化还原反应概念发展史.md
├── 002.元素的氧化数及其规则.md
└── 003.离子-电子法配平氧化还原方程式.md
```

每个 output 对应一个 KnowledgeNode。文件开头的先修前置由程序根据 DAG 自动生成，Writer 不需要自己编写前置关系。

## Embedding 服务

TREE 的 RAG 默认使用本地 OpenAI-compatible embeddings endpoint：

```text
http://localhost:8788/v1/embeddings
```

项目内置 Qwen3-Embedding-4B GGUF server：

```bash
python -m tree.rag.server
```

常用启动方式：

```bash
# 默认：0.0.0.0:8788，尽量使用 GPU/Metal
python -m tree.rag.server

# CPU only
python -m tree.rag.server --n-gpu-layers 0

# 指定地址和端口
python -m tree.rag.server --host 127.0.0.1 --port 8788
```

默认模型：

```text
Qwen/Qwen3-Embedding-4B-GGUF
Qwen3-Embedding-4B-Q8_0.gguf
```

首次启动时，如果本地 Hugging Face cache 没有模型，`llama-cpp-python` 会尝试下载。也可以指定本地模型路径：

```bash
EMBED_MODEL_PATH=/path/to/Qwen3-Embedding-4B-Q8_0.gguf python -m tree.rag.server
```

Embedding 相关环境变量：

```bash
EMBED_API_URL=http://localhost:8788
EMBED_MODEL=Qwen3-Embedding-4B-Q8_0
EMBED_MODEL_PATH=
```

健康检查：

```bash
curl http://localhost:8788/health
```

测试 embedding：

```bash
curl -X POST http://localhost:8788/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-Embedding-4B-Q8_0","input":"化学平衡状态是正逆反应速率相等的状态"}'
```

如果你希望在 macOS 上使用 Metal，需要确保 `llama-cpp-python` 是带 Metal 支持编译安装的版本。

## 常用命令

### Workspace

```bash
tre init                 # 创建 materials/ outputs/ .tree/
tre doctor               # 只读体检
tre setup                # 交互式全局配置向导
tre setup --workspace    # 交互式当前工作区覆盖配置
tre models               # 查看五个角色当前模型
tre prompts              # 列出内置 prompt 角色名
tre clean                # 删除 .tree/runtime/，不删除 materials/ 和 outputs/
```

### Run

```bash
tre                      # 进入 TREE> shell
tre run                  # 前台运行完整 pipeline
tre start                # 后台启动 engine
tre stop                 # 停止后台 engine
tre quit                 # 停止后台 engine
tre resume               # 等同于 tre run
tre continue             # 等同于 tre run
```

### Inspect

```bash
tre status               # 简短状态：phase/message/materials/nodes/edges/active nodes
tre progress             # 打印完整 progress.json
tre watch                # 显示七阶段进度条
tre materials            # 列出支持的资料文件
tre logs                 # 列出 runtime log 文件
```

### Ingest / Planner

```bash
tre ingest --input /path/to/file.pdf --collection 课件
tre ingest --input /path/to/folder --collection 课件
tre planner rebuild
tre planner dag-svg
```

### RAG

```bash
tre rag status
tre rag inventory
tre rag nodes
tre rag graph
tre rag search "化学平衡常数" --top-k 5
```

## 工作区结构

```text
my-course/
├── materials/                 # 输入资料；默认不纳入 git
├── outputs/                   # PASS 后教材 Markdown；默认不纳入 git
└── .tree/
    ├── config.env             # 当前 workspace 覆盖配置
    └── runtime/
        ├── ocr/               # OCR Markdown checkpoint
        ├── source/            # cleaned Markdown，中间态；embedding 后删除
        ├── planner/
        │   ├── material-manifest.json
        │   ├── mtus.json
        │   ├── knowledge-nodes.json
        │   ├── knowledge-dag.json
        │   └── knowledge-dag.svg
        ├── rag-store/         # Qdrant embedded store
        ├── drafts/            # NodeRun 未 PASS 草稿
        ├── pipeline-state.json
        ├── progress.json
        ├── knowledge-ledger.json
        ├── pipeline-temp/
        └── services/
```

全局目录：

```text
~/.tree/
├── config.env                 # 全局默认配置
└── services/                  # 可放全局服务 pid/log
```

## 关键契约

### Archivist / MTU

Archivist 的 `cut_mtus` 输出必须满足：

- 只能输出 `units`，不允许 `skipped_ranges`。
- 第一个 unit 的 `start_line` 必须为 `1`。
- 最后一个 unit 的 `end_line` 必须为 `LAST_VALID_LINE`。
- 相邻 unit 必须首尾相接：下一个 `start_line = 上一个 end_line + 1`。
- 最终 concept MTU 必须至少 20 行。
- 最终 concept MTU 必须有 1-4 个 `defines`。
- `defines` 只能是本 MTU 新引入的定义、公式、方法、模型或定律，不是普通关键词。
- 同一次 cut 调用内不能出现重复 normalized define。

Repair 顺序固定为：

```text
coverage -> short_unit -> metadata
```

只有前一阶段问题全部清空，才会进入下一阶段。

### Dagger / DAG

Dagger 不直接返回最终 edges。当前构图流程是：

1. MTU embedding 和 shared defines 生成候选 cluster。
2. Dagger 判断每个 cluster 应合并还是拆分为 node。
3. Dagger 输出 node `defines`，但每个 define 必须来自该 node 成员 MTU 的原始 defines。
4. 程序建立全局 define 字典。
5. Dagger 为每个 node 从字典中选择 `required_defines`。
6. 程序把 `required_defines -> defining nodes` 映射为 prerequisite edges。
7. 程序移除自依赖、祖先冗余边，并在 cycle 时触发 repair。

数量限制：

- node `defines` 最多 8 个。
- 每个 node 的 `required_defines` 最多 24 个。
- `external_prerequisites` 可以记录资料外基础知识，但不参与 DAG 构边。

### NodeRun

NodeRun 运行约束：

- 一个 NodeRun 只覆盖一个 KnowledgeNode。
- 只有所有 prerequisite parent 都已 PASS 的 node 才会被调度。
- 最多 5 个 active node 并行。
- Examiner 返回的 `Covered_Node_IDs` 会被程序强制收敛为当前 node。
- Writer / Examiner source RAG 只按当前 node_id 检索 source MTU。
- Student 不接收 source RAG，也不接收所有先修输出全文；只接收当前草稿和已完成 ancestor output 的 Qdrant 命中片段。
- PASS 后 output 平铺保存到 `outputs/NNN.title.md`。

## 开发与测试

常用验证命令：

```bash
PYTHONPATH=tree_engine .venv/bin/python -m pytest -q
PYTHONPATH=tree_engine .venv/bin/python -m ruff check tree_engine tests
PYTHONPATH=tree_engine .venv/bin/python -m compileall -q tree_engine/tree
```

常用 focused 测试：

```bash
PYTHONPATH=tree_engine .venv/bin/python -m pytest -q tests/test_dag.py
PYTHONPATH=tree_engine .venv/bin/python -m pytest -q tests/test_agents.py
PYTHONPATH=tree_engine .venv/bin/python -m pytest -q tests/test_branch_run.py
PYTHONPATH=tree_engine .venv/bin/python -m pytest -q tests/test_step9_dashboard_cli.py
```

主要模块：

- `tree.config`：配置加载、角色模型、pipeline knobs。
- `tree.io.paths`：workspace、runtime、planner artifact、service pid/log 路径。
- `tree.ingest`：资料类型检测、OCR engine、PDF / 图片 / DOCX / PPTX / TXT 抽取。
- `tree.agents`：Archivist、Dagger、Examiner、Student、Writer 和结构化输出解析。
- `tree.planner.mtu`：MTU 行覆盖校验、metadata 规范化、稳定 ID。
- `tree.planner.dag`：cluster refinement、define repair、prerequisite 校验、DAG 构建和断环。
- `tree.planner.pipeline`：material manifest、增量 MTU 缓存、planner artifacts 持久化。
- `tree.planner.schedule`：ready node 调度。
- `tree.planner.svg`：静态 DAG SVG 渲染。
- `tree.rag`：chunking、embedding client、Qdrant client、source / finished indexing。
- `tree.engine.ingest_driver`：OCR -> Archivist -> source RAG -> planner 编排。
- `tree.engine.node_run`：Examiner / Student / Writer 循环、PASS 落盘、ledger 更新。
- `tree.engine.orchestrator`：完整 foreground run loop。
- `tree.cli`：Typer CLI、REPL、状态查看、生命周期命令和 dashboard 文本渲染。
- `tree.observability`：progress、retry/backoff、iteration limiter、JSONL trace helper。

## 故障排查

### `tre` 找不到命令

如果刚运行过 `pipx ensurepath`，请重新打开终端，然后检查：

```bash
pipx list
which tre
```

Windows PowerShell：

```powershell
pipx list
Get-Command tre
```

源码 checkout 中也可以直接使用：

```bash
.venv/bin/tre --help
```

### 缺少 LLM 配置

如果看到类似：

```text
No LLM_API_KEY or role-specific API key found
```

请检查：

```bash
tre models
cat .tree/config.env
```

至少需要设置 `LLM_API_KEY`，或设置角色级 `EXAMINER_API_KEY` / `STUDENT_API_KEY` / `WRITER_API_KEY` / `ARCHIVIST_API_KEY` / `DAGGER_API_KEY`。

### PaddleOCR 未配置

请确认：

```bash
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_API_TOKEN=...
```

如果 OCR API 可访问但资料为空或格式不支持，`tre materials` 可以帮助确认当前 `materials/` 中有哪些文件会被 TREE 处理。

### RAG indexer unavailable

完整端到端运行需要安装 `[rag]` 并启动 embedding server：

```bash
pip install -e ".[rag,dev]"
python -m tree.rag.server
```

另开一个终端回到同一 workspace 后运行：

```bash
tre run
```

### 清理运行时产物

```bash
tre clean
```

`tre clean` 只删除 `.tree/runtime/`，不会删除 `materials/` 或 `outputs/`。如果要重新从 OCR 开始验收，通常先执行 `tre clean`，再重新运行 `tre planner rebuild` 或 `tre run`。

## License

MIT
