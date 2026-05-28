# tree

**Exam-driven textbook generation from raw course materials.**

[中文](#中文) | [English](#english)

---

## 中文

tree（Textbook Refinement & Enhancement Engine）是一套资料驱动的自动化教材生成流水线。用户把课件、习题、讲义、图片或文本资料放入 `raw_materials/` 后，引擎会先完成 OCR、结构化整理和本地向量化入库，再通过“以考促写”的循环持续生成教材内容。

核心循环是：

1. `Archivist` 将 OCR 结果清洗为结构化 Markdown。
2. `Examiner` 从 source RAG 中发现下一个知识点并命题。
3. `Student` 只基于已完成教材和当前草稿作答。
4. `Examiner` 批改并定位知识缺口。
5. `Writer` 根据抽象缺陷报告创建或优化教材草稿。
6. 通过后写入 `finished_outputs/`，并继续下一个知识点。

当前实现是独立 Python 编排器，agent prompts 内置在 `tree/agents/prompts.py` 中，无需外部 agent 配置文件。

### 当前特性

- 独立 CLI：`tree-run`
- OpenAI-compatible Chat Completions API，供应商由 `.env` 配置
- PaddleOCR API v2，默认模型 `PaddleOCR-VL-1.6`
- 本地 embedding server：`Qwen3-Embedding-4B-Q8_0` GGUF
- embedded Qdrant 向量库，默认目录 `rag-store/`
- source materials 入库后删除中间 Markdown
- finished outputs 保留原文件并写入 RAG
- draft 不写入 RAG，Student 直接读取当前 draft 全文

### 项目结构

```text
tree/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── tree/                    # 主引擎
│   ├── cli.py               # tree-run CLI
│   ├── engine.py            # Step 0-4 编排循环
│   ├── ingest.py            # 引擎集成摄入流程
│   ├── agents/              # examiner/student/writer/archivist
│   ├── model/               # OpenAI-compatible LLM client
│   ├── io/                  # 文件、source、git 操作
│   ├── observability/       # trace、retry、iteration limiter
│   ├── rag/                 # RAG client/indexer
│   └── state/               # pipeline-state 数据模型
├── rag/                     # 本地 embedding 服务与 chunker
│   ├── server.py
│   ├── embed.py
│   └── chunker.py
├── ingest/                  # 底层 OCR/结构化摄入模块
└── scripts/
    ├── setup-embedding.sh
    ├── start-embed-server.sh
    ├── start-embed-server.bat
    └── run-ingest.sh
```

运行时目录会自动创建，并默认被 `.gitignore` 排除：

```text
raw_materials/          # 用户上传原始资料
source_materials/       # OCR/Archivist 中间 Markdown，入库后删除
drafts/                 # 当前知识点草稿
finished_outputs/       # 通过考试的最终教材
pipeline-state.json     # 流水线状态
pipeline-temp/          # trace、manifest、格式失败记录
rag-store/              # embedded Qdrant 数据库
```

### 安装

安装前需要准备：

- Python `>=3.12`。终端中运行 `python3.12 --version` 能看到版本号即可。
- Git。终端中运行 `git --version` 能看到版本号即可。
- 一个 OpenAI-compatible Chat Completions API key。DeepSeek、OpenAI 或自托管兼容网关都可以。
- PaddleOCR API token。
- 能访问 Hugging Face 或已配置代理，因为首次启动 embedding server 会下载本地 embedding 模型。

如果本机还没有 Python 或 Git，先安装 [Python](https://www.python.org/downloads/) 和 [Git](https://git-scm.com/downloads)。安装后重新打开终端，再运行上面的版本检查命令。

1. 克隆仓库：

```bash
git clone https://github.com/Waylon524/tree.git
cd tree
```

2. 创建并进入 Python 虚拟环境：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell 使用：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. 升级基础安装工具：

```bash
pip install -U pip
```

4. 安装 tree 引擎和 RAG/embedding 依赖：

```bash
pip install -e ".[rag]"
```

这个命令会把当前仓库安装为可编辑模式，并注册 `tree-run` 命令。后续修改源码后无需重新安装。

如果你只是开发或调试，也可以安装开发依赖：

```bash
pip install -e ".[rag,dev]"
```

5. 确认 CLI 可用：

```bash
tree-run --help
```

如果 `tree-run` 暂时不可用，说明当前 shell 还没有识别虚拟环境中的命令。先确认已经执行 `source .venv/bin/activate`，也可以临时使用：

```bash
PYTHONPATH=. python -m tree.cli --help
```

6. 安装并启动本地 embedding server。继续阅读下一节“本地 Embedding 模型”。

7. 配置 API key 和模型。继续阅读“环境变量”一节；第一次运行 `tree-run run` 时也会自动弹出配置向导。

8. 把课件、习题或讲义放入 `raw_materials/`，然后运行：

```bash
tree-run run
```

### 本地 Embedding 模型

tree 默认使用 `Qwen/Qwen3-Embedding-4B-GGUF` 中的 `Qwen3-Embedding-4B-Q8_0.gguf`。首次启动 embedding server 时会自动下载模型，文件大小约 4.3 GB。

安装 `llama-cpp-python` 和服务依赖：

```bash
./scripts/setup-embedding.sh
```

Apple Silicon 推荐：

```bash
./scripts/setup-embedding.sh --device metal
```

CPU-only：

```bash
./scripts/setup-embedding.sh --device cpu
```

NVIDIA CUDA：

```bash
./scripts/setup-embedding.sh --device cuda
```

启动 embedding server：

```bash
./scripts/start-embed-server.sh
```

默认配置：

```text
EMBED_PORT=8788
EMBED_N_GPU_LAYERS=-1
EMBED_N_CTX=32768
EMBED_N_SEQ_MAX=1
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

### 环境变量

第一次在某个工作区中运行需要配置的命令时，例如 `tree-run run`、`tree-run ingest` 或 `tree-run doctor`，如果当前目录没有 `.env`，CLI 会自动启动交互式配置向导。向导会在命令行中要求输入：

- PaddleOCR API key
- 子智能体共享 API key
- LLM base URL
- 默认模型
- `Examiner`、`Student`、`Writer`、`Archivist` 四个角色的模型
- PaddleOCR model

你也可以手动启动向导：

```bash
tree-run setup
tree-run setup --force
```

后续修改模型和供应商配置使用：

```bash
tree-run models
tree-run models --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash
tree-run models --examiner deepseek-v4-flash --student deepseek-v4-flash
tree-run models --api-key
tree-run models --paddleocr-key
```

CLI 会把配置写入当前工作区的 `.env`。`.env` 已被 Git 忽略，不应提交到仓库。

生成的 `.env` 大致如下：

```bash
# OpenAI-compatible LLM
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash

# Optional role-specific overrides
EXAMINER_MODEL=deepseek-v4-flash
STUDENT_MODEL=deepseek-v4-flash
WRITER_MODEL=deepseek-v4-flash
ARCHIVIST_MODEL=deepseek-v4-flash

# PaddleOCR
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_API_TOKEN=your-paddleocr-token
PADDLEOCR_MODEL=PaddleOCR-VL-1.6

# Source ingest
SOURCE_INGEST_CONCURRENCY=16
SOURCE_OCR_CONCURRENCY=16
SOURCE_OCR_UPLOAD_INTERVAL_SEC=5
SOURCE_ARCHIVIST_CONCURRENCY=16
SOURCE_EMBEDDING_CONCURRENCY=1
SOURCE_ARCHIVIST_CHUNK_CHARS=24000

# Local embedding server
EMBED_API_URL=http://localhost:8788
EMBED_MODEL=Qwen3-Embedding-4B-Q8_0
EMBED_PORT=8788
EMBED_N_CTX=32768
EMBED_N_GPU_LAYERS=-1
EMBED_N_SEQ_MAX=1
```

`LLM_BASE_URL` 和模型名只是示例。任何兼容 OpenAI Chat Completions API 的供应商或自托管网关都可以使用。每个角色也支持独立配置 `EXAMINER_API_KEY`、`EXAMINER_BASE_URL`、`EXAMINER_MODEL`，`STUDENT_*`、`WRITER_*`、`ARCHIVIST_*` 同理。

### 使用方法

把资料放入 `raw_materials/`。子目录名会作为 source collection：

```text
raw_materials/
├── 课件/
│   ├── 5. 化学平衡通论.pdf
│   └── 6. 化学动力学简介.pdf
└── 作业/
    ├── 普通化学A-作业2026-01.pdf
    └── 普通化学A-作业2026-02.pdf
```

支持 PDF、图片、DOCX、Markdown、TXT 等格式，具体以后缀集合 `tree.engine.RAW_MATERIAL_EXTENSIONS` 为准。

启动流水线：

```bash
source .venv/bin/activate
tree-run run
```

每次 `tree-run run` 都会先检查 `raw_materials/`：

- 有新增或变更资料：先执行 OCR -> Archivist -> source embedding。
- 第一个 source material 生成后即可开始串行 embedding。
- 所有 source materials embedding 完成后，才进入考试-写作循环。
- 没有新资料：直接从 `pipeline-state.json` 恢复循环。

断点恢复：

```bash
tree-run resume
```

手动摄入某个文件或目录：

```bash
tree-run ingest --input raw_materials/课件 --collection 课件
tree-run ingest --input raw_materials/课件 --collection 课件 --no-structure
tree-run ingest --input raw_materials/课件 --collection 课件 --no-index
```

### CLI 命令

```bash
tree-run --help
tree-run run
tree-run resume
tree-run status
tree-run status --verbose
tree-run doctor
tree-run materials
tree-run logs --tail 20
tree-run prompts writer
tree-run prompts examiner --full
tree-run setup
tree-run models
tree-run models --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash
tree-run clean --dry-run
tree-run clean --apply --pycache
tree-run rag status
tree-run rag search "化学平衡常数" --kind source --top-k 5
```

命令说明：

| 命令 | 作用 |
| --- | --- |
| `run` | 启动完整流水线 |
| `resume` | 从现有状态继续 |
| `status` | 查看章节进度 |
| `doctor` | 检查环境、服务和 Git 状态 |
| `materials` | 查看 raw materials 摄入状态 |
| `logs` | 查看 trace 日志 |
| `prompts` | 查看内置 agent prompts |
| `setup` | 交互式创建或更新 `.env` |
| `models` | 查看或修改模型、base URL、API key |
| `clean` | 清理项目缓存和运行中间目录 |
| `ingest` | 手动摄入文件或目录 |
| `rag status` | 查看 RAG chunk 概况 |
| `rag search` | 手动检索 RAG |

### Agent 工作流

| Role | Prompt | 作用 |
| --- | --- | --- |
| Examiner | `EXAMINER_PROMPT` | 发现章节/知识点、命题、批改、判断 PASS/FAIL |
| Student | `STUDENT_PROMPT` | 零基础学生，只基于已学内容和当前草稿作答 |
| Writer | `WRITER_PROMPT` | 根据抽象 Bottleneck Report 创建或优化教材草稿 |
| Archivist | `ARCHIVIST_PROMPT` | 对 PaddleOCR 输出做轻量清洗和 Markdown 标准化 |

流程：

```text
raw materials
  -> PaddleOCR-VL-1.6
  -> Archivist cleanup
  -> source RAG
  -> Examiner exam assembly
  -> Student blind test
  -> Examiner audit
  -> Writer create/optimize
  -> finished_outputs
```

### RAG 策略

- source materials 写入 RAG 后删除中间 Markdown。
- finished outputs 保留原文件，同时写入 RAG。
- drafts 不写入 RAG，Student 直接读取当前 draft 全文。
- chunker 使用 1500-3000 token 左右的语义块，再在查询命中后扩展相邻 chunk。

当前 chunk 预算：

```python
MAX_TOKENS = {
    "def": 2000,
    "proof": 3000,
    "example": 2400,
    "narrative": 1500,
}
```

### PaddleOCR-VL-1.6

默认模型：

```text
PADDLEOCR_MODEL=PaddleOCR-VL-1.6
```

OCR job 使用：

```python
optionalPayload = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}
```

OCR 上传默认每 5 秒提交一个文件；上传和轮询可以并发，embedding 默认串行。

### 验证

当前仓库不保留内置样例数据和单元测试目录。修改代码后建议至少执行：

```bash
ruff check tree rag ingest
python -m compileall tree rag ingest
```

需要端到端验证时，将真实资料放入 `raw_materials/`，启动 embedding server，然后运行：

```bash
tree-run run
```

### 常见问题

**`Source materials exist but RAG indexer is unavailable`**

说明 embedding server 未启动或 RAG 依赖未安装。

```bash
pip install -e ".[rag]"
./scripts/start-embed-server.sh
```

**`tree-run` 无法导入本地包**

先确认已经在虚拟环境中安装当前项目：

```bash
source .venv/bin/activate
pip install -e .
```

源码调试时也可临时使用：

```bash
PYTHONPATH=. python -m tree.cli --help
```

**GitHub 仓库名**

远端路径已更新为：

```text
https://github.com/Waylon524/tree.git
```

GitHub 页面显示的仓库名是 `tree`。

### License

MIT. See [LICENSE](LICENSE).

---

## English

tree (Textbook Refinement & Enhancement Engine) is a material-driven pipeline for generating textbook chapters through exam-driven writing. After users place lecture slides, exercises, handouts, images, or text files in `raw_materials/`, the engine performs OCR, lightweight structuring, local embedding, and then runs an iterative teaching loop.

The core loop is:

1. `Archivist` cleans OCR output into structured Markdown.
2. `Examiner` discovers the next knowledge point from source RAG and composes an exam.
3. `Student` answers using only learned finished outputs and the current draft.
4. `Examiner` grades the answer and reports knowledge gaps.
5. `Writer` creates or optimizes the draft from an abstract bottleneck report.
6. Passing drafts move to `finished_outputs/`, and the loop continues.

The current runtime is a standalone Python orchestrator. Agent prompts are built into `tree/agents/prompts.py`; the engine does not require external agent configuration files.

### Features

- Standalone CLI: `tree-run`
- OpenAI-compatible Chat Completions API, configured through `.env`
- PaddleOCR API v2, default model `PaddleOCR-VL-1.6`
- Local embedding server with `Qwen3-Embedding-4B-Q8_0` GGUF
- Embedded Qdrant vector store at `rag-store/`
- Source Markdown is deleted after successful source embedding
- Finished outputs are kept on disk and indexed into RAG
- Drafts are not indexed; the Student reads the current draft directly

### Repository Layout

```text
tree/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── tree/                    # Main engine
│   ├── cli.py               # tree-run CLI
│   ├── engine.py            # Step 0-4 orchestration loop
│   ├── ingest.py            # Engine-integrated ingest flow
│   ├── agents/              # examiner/student/writer/archivist
│   ├── model/               # OpenAI-compatible LLM client
│   ├── io/                  # file/source/git operations
│   ├── observability/       # trace, retry, iteration limiter
│   ├── rag/                 # RAG client/indexer
│   └── state/               # pipeline-state models
├── rag/                     # Local embedding server and chunker
├── ingest/                  # Low-level OCR/structuring ingest modules
└── scripts/                 # Setup and runtime helper scripts
```

Runtime paths are created automatically and ignored by Git:

```text
raw_materials/
source_materials/
drafts/
finished_outputs/
pipeline-state.json
pipeline-temp/
rag-store/
```

### Installation

Before installation, prepare:

- Python `>=3.12`. `python3.12 --version` should print a version number.
- Git. `git --version` should print a version number.
- An OpenAI-compatible Chat Completions API key. DeepSeek, OpenAI, or a self-hosted compatible gateway can be used.
- A PaddleOCR API token.
- Access to Hugging Face, or a configured proxy, because the local embedding model is downloaded on first start.

If Python or Git is missing, install [Python](https://www.python.org/downloads/) and [Git](https://git-scm.com/downloads/) first. Reopen your terminal after installation, then run the version checks above.

1. Clone the repository:

```bash
git clone https://github.com/Waylon524/tree.git
cd tree
```

2. Create and activate a Python virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. Upgrade the base installer:

```bash
pip install -U pip
```

4. Install the tree engine and RAG/embedding dependencies:

```bash
pip install -e ".[rag]"
```

This installs the current checkout in editable mode and registers the `tree-run` command. Source changes take effect without reinstalling.

For development and linting, install the development extras:

```bash
pip install -e ".[rag,dev]"
```

5. Confirm that the CLI is available:

```bash
tree-run --help
```

If `tree-run` is not found, make sure the virtual environment is active. You can also use this source-checkout fallback:

```bash
PYTHONPATH=. python -m tree.cli --help
```

6. Install and start the local embedding server. See the next section, "Local Embedding Model".

7. Configure API keys and model names. See "Environment"; the first `tree-run run` also starts the setup wizard automatically.

8. Put lectures, exercises, or handouts into `raw_materials/`, then run:

```bash
tree-run run
```

### Local Embedding Model

tree uses `Qwen3-Embedding-4B-Q8_0.gguf` from `Qwen/Qwen3-Embedding-4B-GGUF` by default. The model is downloaded automatically on the first embedding server start. The file is about 4.3 GB.

Install `llama-cpp-python` and service dependencies:

```bash
./scripts/setup-embedding.sh
```

Apple Silicon:

```bash
./scripts/setup-embedding.sh --device metal
```

CPU-only:

```bash
./scripts/setup-embedding.sh --device cpu
```

NVIDIA CUDA:

```bash
./scripts/setup-embedding.sh --device cuda
```

Start the embedding server:

```bash
./scripts/start-embed-server.sh
```

Default settings:

```text
EMBED_PORT=8788
EMBED_N_GPU_LAYERS=-1
EMBED_N_CTX=32768
EMBED_N_SEQ_MAX=1
```

Health check:

```bash
curl http://localhost:8788/health
```

Embedding test:

```bash
curl -X POST http://localhost:8788/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-Embedding-4B-Q8_0","input":"chemical equilibrium"}'
```

### Environment

The first time you run a configuration-dependent command in a workspace, such as `tree-run run`, `tree-run ingest`, or `tree-run doctor`, the CLI starts an interactive setup wizard if `.env` does not exist. The wizard asks for:

- PaddleOCR API key
- shared API key for the agent provider
- LLM base URL
- default model
- role models for `Examiner`, `Student`, `Writer`, and `Archivist`
- PaddleOCR model

You can also start the wizard manually:

```bash
tree-run setup
tree-run setup --force
```

Update model/provider settings later with:

```bash
tree-run models
tree-run models --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash
tree-run models --examiner deepseek-v4-flash --student deepseek-v4-flash
tree-run models --api-key
tree-run models --paddleocr-key
```

The CLI writes settings to the current workspace's `.env`. `.env` is ignored by Git and should not be committed.

The generated `.env` looks roughly like this:

```bash
# OpenAI-compatible LLM
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash

# Optional role-specific overrides
EXAMINER_MODEL=deepseek-v4-flash
STUDENT_MODEL=deepseek-v4-flash
WRITER_MODEL=deepseek-v4-flash
ARCHIVIST_MODEL=deepseek-v4-flash

# PaddleOCR
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_API_TOKEN=your-paddleocr-token
PADDLEOCR_MODEL=PaddleOCR-VL-1.6

# Local embedding server
EMBED_API_URL=http://localhost:8788
EMBED_MODEL=Qwen3-Embedding-4B-Q8_0
EMBED_PORT=8788
EMBED_N_CTX=32768
EMBED_N_GPU_LAYERS=-1
EMBED_N_SEQ_MAX=1
```

The DeepSeek URL and model above are examples only. Any OpenAI-compatible Chat Completions provider can be used. Role-specific keys, base URLs, and models are also supported through `EXAMINER_*`, `STUDENT_*`, `WRITER_*`, and `ARCHIVIST_*`.

### Usage

Place source files in `raw_materials/`. Subdirectories become source collections:

```text
raw_materials/
├── lectures/
│   ├── 05-equilibrium.pdf
│   └── 06-kinetics.pdf
└── exercises/
    ├── homework-01.pdf
    └── homework-02.pdf
```

Start the pipeline:

```bash
source .venv/bin/activate
tree-run run
```

On every start, `tree-run run` checks `raw_materials/`:

- new or changed materials are processed through OCR -> Archivist -> source embedding
- embedding starts as soon as the first source material is produced
- the exam-writing loop starts only after all source materials are embedded
- if no new material exists, the loop resumes from `pipeline-state.json`

Resume:

```bash
tree-run resume
```

Manual ingest:

```bash
tree-run ingest --input raw_materials/lectures --collection lectures
tree-run ingest --input raw_materials/lectures --collection lectures --no-structure
tree-run ingest --input raw_materials/lectures --collection lectures --no-index
```

### CLI Commands

```bash
tree-run --help
tree-run run
tree-run resume
tree-run status
tree-run status --verbose
tree-run doctor
tree-run materials
tree-run logs --tail 20
tree-run prompts writer
tree-run prompts examiner --full
tree-run setup
tree-run models
tree-run models --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash
tree-run clean --dry-run
tree-run clean --apply --pycache
tree-run rag status
tree-run rag search "equilibrium constant" --kind source --top-k 5
```

| Command | Purpose |
| --- | --- |
| `run` | Start the full pipeline |
| `resume` | Continue from existing state |
| `status` | Show chapter progress |
| `doctor` | Check configuration, services, and Git status |
| `materials` | Show raw material ingest status |
| `logs` | Inspect trace logs |
| `prompts` | Inspect built-in agent prompts |
| `setup` | Create or update `.env` interactively |
| `models` | Show or update models, base URL, and API keys |
| `clean` | Clean project caches and runtime artifacts |
| `ingest` | Manually ingest files or directories |
| `rag status` | Show indexed RAG chunks |
| `rag search` | Query the local RAG index |

### Agent Workflow

| Role | Prompt | Purpose |
| --- | --- | --- |
| Examiner | `EXAMINER_PROMPT` | Finds knowledge points, composes exams, audits answers |
| Student | `STUDENT_PROMPT` | Zero-baseline learner using only learned materials and current draft |
| Writer | `WRITER_PROMPT` | Creates or optimizes drafts from abstract bottleneck reports |
| Archivist | `ARCHIVIST_PROMPT` | Cleans PaddleOCR output into normalized Markdown |

```text
raw materials
  -> PaddleOCR-VL-1.6
  -> Archivist cleanup
  -> source RAG
  -> Examiner exam assembly
  -> Student blind test
  -> Examiner audit
  -> Writer create/optimize
  -> finished_outputs
```

### RAG Strategy

- Source materials are deleted from `source_materials/` after indexing.
- Finished outputs remain in `finished_outputs/` and are indexed.
- Drafts are not indexed.
- Retrieval uses semantic chunks plus adjacent chunk expansion.

Chunk budgets:

```python
MAX_TOKENS = {
    "def": 2000,
    "proof": 3000,
    "example": 2400,
    "narrative": 1500,
}
```

### Verification

This repository no longer ships built-in sample data or a unit test directory. For code changes, run at least:

```bash
ruff check tree rag ingest
python -m compileall tree rag ingest
```

For end-to-end verification, place real materials in `raw_materials/`, start the embedding server, and run:

```bash
tree-run run
```

### FAQ

**`Source materials exist but RAG indexer is unavailable`**

Start the embedding server and make sure RAG dependencies are installed:

```bash
pip install -e ".[rag]"
./scripts/start-embed-server.sh
```

**`tree-run` cannot import the local package**

Install the project into the active environment:

```bash
source .venv/bin/activate
pip install -e .
```

Or use the source-checkout fallback:

```bash
PYTHONPATH=. python -m tree.cli --help
```

**GitHub repository name**

The remote URL is now:

```text
https://github.com/Waylon524/tree.git
```

The GitHub repository name is displayed as `tree`.

### License

MIT. See [LICENSE](LICENSE).
