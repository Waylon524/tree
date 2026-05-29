# tree

**Exam-driven textbook generation from raw course materials.**

[中文](#中文) | [English](#english)

---

## 中文

### 1. tree 是什么

tree（Textbook Refinement & Enhancement Engine）是一套资料驱动的自动化教材生成流水线。用户把课件、习题、讲义、图片或文本资料放入 `raw_materials/` 后，引擎会自动完成 OCR、结构化整理、本地向量化入库，并通过“以考促写”的循环持续生成教材内容。

核心流程：

```text
raw_materials/
  -> PaddleOCR-VL-1.6
  -> Archivist 结构化清洗
  -> source RAG
  -> Examiner 命题
  -> Student 盲测
  -> Examiner 批改
  -> Writer 创建或优化教材
  -> finished_outputs/
```

当前实现是独立 Python 编排器。Agent prompts 内置在 `tree_engine/tree/agents/prompts.py`，不需要 `.claude/`、`AGENTS.md` 或外部子智能体配置文件。

### 2. 当前特性

- 独立 CLI：`tree-run`，支持交互式 slash commands。
- OpenAI-compatible Chat Completions API，供应商和模型由 `.env` 配置。
- PaddleOCR API v2，固定 job URL，默认模型 `PaddleOCR-VL-1.6`。
- 本地 embedding server：`Qwen3-Embedding-4B-Q8_0` GGUF。
- embedded Qdrant 向量库，默认目录 `tree_engine/.runtime/rag-store/`。
- source materials 写入 RAG 后删除中间 Markdown。
- finished outputs 保留原文件，并写入 RAG。
- draft 不写入 RAG，Student 直接读取当前 draft 全文。

### 3. 快速开始

#### 3.1 首次安装（无依赖）

适用于：这台电脑从未安装过 tree 的依赖，也没有下载过本地 embedding 模型。

开始前需要准备：

- Python `>=3.12`，运行 `python3.12 --version` 能看到版本号。
- Git，运行 `git --version` 能看到版本号。
- 一个 OpenAI-compatible Chat Completions API key。DeepSeek、OpenAI 或自托管兼容网关都可以。
- PaddleOCR API token。
- 能访问 Hugging Face 或已配置代理，因为首次启动 embedding server 会下载约 4.3 GB 的本地模型。

如果本机还没有 Python 或 Git，先安装 [Python](https://www.python.org/downloads/) 和 [Git](https://git-scm.com/downloads/)。安装后重新打开终端，再运行上面的版本检查命令。

克隆仓库：

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
```

如果看到 `fatal: destination path 'engine' already exists and is not an empty directory`，说明当前目录已经有一个叫 `engine` 的文件夹。请先停下来，不要继续 `cd engine` 后再执行一次 `git clone ... engine`，否则会变成 `engine/engine` 嵌套安装。请选择一个新的空目录名，例如 `engine-new`，或先删除/重命名旧的 `engine`。

后续命令都必须在**项目根目录**执行。项目根目录里应该能看到：

```text
pyproject.toml
README.md
raw_materials/
finished_outputs/
tree_engine/
```

可以检查：

```bash
pwd
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell：

```powershell
Get-Location
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

推荐直接运行 bootstrap：

```bash
./tree_engine/scripts/bootstrap.sh
```

Windows PowerShell：

```powershell
.\tree_engine\scripts\bootstrap.ps1
```

bootstrap 会检查 Python、系统类型、Apple Silicon / CUDA / CPU 设备提示、依赖安装状态和 CLI 可用性；创建 `.venv`；安装依赖；启动 `tree-run setup`；并在 setup 完成后把 embedding server 启动到后台。首次启动可能下载约 4.3 GB 模型，下载和加载过程会显示在终端中。

bootstrap 结束后，当前终端不会自动进入 `.venv`。可以直接运行虚拟环境里的入口：

```bash
.venv/bin/tree-run
```

Windows PowerShell：

```powershell
.\.venv\Scripts\tree-run.exe
```

进入 `TREE>` 后，把资料放入 `raw_materials/`，然后输入：

```text
/continue
/watch
```

如果先手动激活虚拟环境，也可以使用短命令：

```bash
source .venv/bin/activate
tree-run
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
tree-run
```

#### 3.2 二次安装（本机已下载过本地模型）

适用于：这台电脑已经成功跑过 tree，Hugging Face 缓存里已有 `Qwen3-Embedding-4B-Q8_0.gguf`，现在只是换一个新工作区。

最稳妥的做法仍然是：**每个工作区创建自己的 `.venv`**。这样不会因为找不到旧虚拟环境目录而卡住，也不会让 `tree-run` 指向旧 checkout。二次安装会复用 pip 缓存和 Hugging Face 模型缓存，通常不会重新下载 4.3 GB 的 embedding 模型。

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
./tree_engine/scripts/bootstrap.sh
```

Windows PowerShell：

```powershell
git clone https://github.com/Waylon524/tree.git engine
cd engine
.\tree_engine\scripts\bootstrap.ps1
```

如果确实想复用旧虚拟环境，先找到旧环境的真实路径：

```bash
find .. -path "*/.venv/bin/activate" -print
```

Windows PowerShell：

```powershell
Get-ChildItem .. -Recurse -Filter Activate.ps1 -ErrorAction SilentlyContinue
```

然后激活找到的路径，并在新工作区重新执行 `pip install ".[rag]"`，让 `tree-run` 指向当前 checkout。不要照抄 `../engine/.venv/bin/activate`，除非你的旧工作区确实在这个位置。

### 4. 日常使用

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

进入交互式 CLI：

```bash
source .venv/bin/activate
tree-run
```

未激活虚拟环境时：

```bash
.venv/bin/tree-run
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
tree-run
```

常用交互命令：

```text
/continue   # 后台启动或继续 TREE，自动确保 embedding server 运行
/watch      # 持续刷新当前进度，按 Ctrl+C 回到 TREE>
/progress   # 显示一屏进度快照
/status     # 查看服务和章节状态
/stop       # 停止 TREE，保留 embedding server
/quit       # 停止 TREE 和 embedding server
/help       # 查看交互命令
```

也可以不进入交互模式，直接运行：

```bash
tree-run continue
tree-run watch
tree-run status
tree-run stop
tree-run quit
```

每次 `/continue` 或 `tree-run continue` 都会先检查 `raw_materials/`：

- 有新增或变更资料：先执行 OCR -> Archivist -> source embedding。
- 第一个 source material 生成后即可开始串行 embedding。
- 所有 source materials embedding 完成后，才进入考试-写作循环。
- 没有新资料：直接从 `tree_engine/.runtime/pipeline-state.json` 恢复循环。

手动摄入某个文件或目录：

```bash
tree-run ingest --input raw_materials/课件 --collection 课件
tree-run ingest --input raw_materials/课件 --collection 课件 --no-structure
tree-run ingest --input raw_materials/课件 --collection 课件 --no-index
```

### 5. 配置 API 和模型

第一次在某个工作区中运行需要配置的命令时，例如 `tree-run continue`、`tree-run ingest` 或 `tree-run doctor`，如果当前目录没有 `.env`，CLI 会自动启动交互式配置向导。你也可以手动运行：

```bash
tree-run setup
tree-run setup --force
```

向导会要求输入：

- PaddleOCR API key。
- 子智能体共享 API key。
- LLM base URL。
- 默认模型。
- `Examiner`、`Student`、`Writer`、`Archivist` 四个角色的模型。

PaddleOCR job URL 和 PaddleOCR model 是固定值，不需要填写。当前固定为：

```text
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_MODEL=PaddleOCR-VL-1.6
```

获取 PaddleOCR API key：

1. 打开 [PaddleOCR API 任务页](https://aistudio.baidu.com/paddleocr/task)。
2. 登录百度 AI Studio / 飞桨账号。
3. 在页面中找到 PaddleOCR-VL 的 API 调用示例或任务创建区域。
4. 复制示例里的 `TOKEN` 或 `Authorization: bearer ...` 后面的 token。
5. 运行 `tree-run setup` 时，把 token 粘贴到 `PaddleOCR API key` 输入项。

不要把 PaddleOCR API key 写进 README、截图或提交到 Git。tree 会把它写入本地 `.env`，该文件已被 `.gitignore` 排除。输入 API key 时终端不会显示任何字符，这是正常的隐藏输入，类似输入密码。配置完成后可以运行 `tree-run models`，它只显示 key 是 `set` / `not set`，不会打印真实密钥。

模型名必须完全匹配供应商支持的名称，不要带空格、颜色控制残留或多余字符。例如 DeepSeek 当前应填写 `deepseek-v4-pro` 或 `deepseek-v4-flash`，不要填写 `deepseek-v4-pro[1m]`。如果填错了，可以修正：

```bash
tree-run models \
  --base-url https://api.deepseek.com/v1 \
  --examiner deepseek-v4-pro \
  --student deepseek-v4-flash \
  --writer deepseek-v4-flash \
  --archivist deepseek-v4-flash
```

后续修改模型和供应商配置：

```bash
tree-run models
tree-run models --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash
tree-run models --examiner deepseek-v4-flash --student deepseek-v4-flash
tree-run models --api-key
tree-run models --paddleocr-key
```

如果 `tree-run setup` 仍然询问 `PaddleOCR job API URL` 或 `PaddleOCR model`，说明当前安装的是旧版本。请在项目根目录运行：

```bash
git pull
source .venv/bin/activate
pip install ".[rag]"
```

Windows PowerShell：

```powershell
git pull
.\.venv\Scripts\Activate.ps1
pip install ".[rag]"
```

生成的 `.env` 大致如下。PaddleOCR URL 和 model 由 CLI 自动写入固定值：

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

### 6. CLI 命令

交互模式：

```bash
tree-run
```

进入 `TREE>` 后可以输入：

```text
/continue
/status
/progress
/watch
/stop
/quit
/logs --tail 20
/materials
/doctor
/models
/rag status
/help
/exit
```

传统一次性命令仍然可用：

```bash
tree-run --help
tree-run continue
tree-run stop
tree-run quit
tree-run run
tree-run resume
tree-run status
tree-run status --verbose
tree-run progress
tree-run watch
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

| 命令 | 作用 |
| --- | --- |
| `run` | 前台启动完整流水线，适合调试 |
| `continue` | 后台启动或继续 TREE，自动确保 embedding server 运行 |
| `stop` | 停止 TREE，保留 embedding server |
| `quit` | 停止 TREE 和 embedding server |
| `resume` | 前台从现有状态继续，适合调试 |
| `status` | 查看服务和章节进度 |
| `progress` | 查看服务、资料入库、章节和最近 trace 的一屏进度 |
| `watch` | 持续刷新当前进度，按 `Ctrl+C` 返回 |
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

### 7. 运行机制

#### 7.1 Agent 工作流

| Role | Prompt | 作用 |
| --- | --- | --- |
| Examiner | `EXAMINER_PROMPT` | 发现章节/知识点、命题、批改、判断 PASS/FAIL |
| Student | `STUDENT_PROMPT` | 零基础学生，只基于已学内容和当前草稿作答 |
| Writer | `WRITER_PROMPT` | 根据抽象 Bottleneck Report 创建或优化教材草稿 |
| Archivist | `ARCHIVIST_PROMPT` | 对 PaddleOCR 输出做轻量清洗和 Markdown 标准化 |

#### 7.2 RAG 策略

- source materials 写入 RAG 后删除 `tree_engine/.runtime/source_materials/` 中的中间 Markdown。
- finished outputs 保留在 `finished_outputs/`，同时写入 RAG。
- drafts 不写入 RAG，Student 直接读取当前 draft 全文。
- Examiner 命题会参考 source RAG 和 finished output RAG。
- Student 答题会使用已学习 finished outputs 的 RAG 检索，并直接阅读当前 draft。
- chunker 使用约 1500-3000 token 的语义块，查询命中后扩展读取相邻 chunk。

当前 chunk 预算：

```python
MAX_TOKENS = {
    "def": 2000,
    "proof": 3000,
    "example": 2400,
    "narrative": 1500,
}
```

#### 7.3 PaddleOCR-VL-1.6

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

OCR 上传默认每 5 秒提交一个文件；上传和轮询可以并发；Archivist 可以多并发处理；embedding 默认串行。

#### 7.4 本地 Embedding 模型

tree 默认使用 `Qwen/Qwen3-Embedding-4B-GGUF` 中的 `Qwen3-Embedding-4B-Q8_0.gguf`。首次启动 embedding server 时会自动下载模型，文件大小约 4.3 GB。下载完成后模型会留在本机 Hugging Face 缓存中，之后换工作区通常不需要重新下载。

`pip install ".[rag]"` 已经安装 embedding server 所需的 Python 依赖。`setup-embedding.sh` 主要用于 macOS / Linux 上重新编译或强制选择 Metal/CUDA/CPU 版本的 `llama-cpp-python`。

```bash
./tree_engine/scripts/setup-embedding.sh
./tree_engine/scripts/setup-embedding.sh --device metal
./tree_engine/scripts/setup-embedding.sh --device cpu
./tree_engine/scripts/setup-embedding.sh --device cuda
```

Windows PowerShell 用户通常不需要运行 `setup-embedding.sh`；它是 macOS / Linux shell 脚本。

bootstrap 和 `/continue` 会后台管理 embedding server。手动前台启动仅建议用于调试：

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell：

```powershell
tree_engine\scripts\start-embed-server.bat
```

前台启动会占用当前终端。运行 `tree-run setup`、`tree-run continue`、`tree-run ingest` 等命令时，请新开终端标签页，回到项目根目录并重新激活虚拟环境。

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

### 8. 项目结构

```text
engine/
├── README.md
├── pyproject.toml
├── raw_materials/          # 用户上传原始资料；目录保留，内容被 Git 忽略
├── finished_outputs/       # 通过考试的最终教材；目录保留，内容被 Git 忽略
└── tree_engine/            # 引擎源码、脚本、RAG、ingest 和内部运行时
    ├── tree/               # 主引擎 Python 包
    ├── rag/                # 本地 embedding 服务与 chunker
    ├── ingest/             # 底层 OCR/结构化摄入模块
    ├── scripts/            # 安装与运行脚本
    └── .runtime/           # 自动创建；中间文件、状态和向量库
```

运行时目录会自动创建，并默认被 `.gitignore` 排除。`raw_materials/` 会保留一个空目录，方便用户直接放资料；`finished_outputs/` 会保留一个空目录，方便用户查看最终教材。两个目录里的真实内容都不会提交到 Git。

```text
raw_materials/          # 用户上传原始资料
finished_outputs/       # 通过考试的最终教材
tree_engine/.runtime/   # source_materials、drafts、pipeline-state、trace、rag-store、services
```

### 9. 验证与排错

当前仓库不保留内置样例数据和单元测试目录。修改代码后建议至少执行：

```bash
ruff check tree_engine/tree tree_engine/rag tree_engine/ingest
python -m compileall tree_engine/tree tree_engine/rag tree_engine/ingest
```

需要端到端验证时，将真实资料放入 `raw_materials/`，然后运行：

```bash
tree-run continue
tree-run watch
```

常见问题：

**`Source materials exist but RAG indexer is unavailable`**

说明 embedding server 未启动或 RAG 依赖未安装。

```bash
pip install ".[rag]"
tree-run continue
```

手动前台调试：

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell：

```powershell
pip install ".[rag]"
tree_engine\scripts\start-embed-server.bat
```

**`tree-run` 找不到命令**

bootstrap 结束后不会自动激活当前终端的虚拟环境。直接运行：

```bash
.venv/bin/tree-run
```

或先激活：

```bash
source .venv/bin/activate
tree-run
```

Windows PowerShell：

```powershell
.\.venv\Scripts\tree-run.exe
.\.venv\Scripts\Activate.ps1
tree-run
```

**`tree-run` 无法导入本地包**

如果看到 `ModuleNotFoundError: No module named 'tree'`，通常是旧的 editable 安装没有正确绑定源码路径。请在项目根目录重新用非 editable 模式安装：

```bash
source .venv/bin/activate
pip install --force-reinstall ".[rag]"
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
pip install --force-reinstall ".[rag]"
```

源码调试时也可临时使用：

```bash
PYTHONPATH=tree_engine python -m tree.cli --help
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "tree_engine"
python -m tree.cli --help
```

**clone 到了错误位置或出现 `engine/engine`**

如果 `git clone ... engine` 提示目标目录已存在，请不要继续 `cd engine` 后再次 clone。bootstrap 已经会拦截 `.Trash` 路径和嵌套 checkout。请换一个空目录名，或删除/重命名旧目录后重新 clone。

**GitHub 仓库名**

远端路径：

```text
https://github.com/Waylon524/tree.git
```

GitHub 页面显示的仓库名是 `tree`。

### 10. License

MIT. See [LICENSE](LICENSE).

---

## English

### 1. What Is tree?

tree (Textbook Refinement & Enhancement Engine) is a material-driven pipeline for generating textbook chapters through exam-driven writing. After users place lecture slides, exercises, handouts, images, or text files in `raw_materials/`, the engine performs OCR, lightweight structuring, local embedding, and an iterative teaching loop.

Core flow:

```text
raw_materials/
  -> PaddleOCR-VL-1.6
  -> Archivist cleanup
  -> source RAG
  -> Examiner exam assembly
  -> Student blind test
  -> Examiner audit
  -> Writer create/optimize
  -> finished_outputs/
```

The current runtime is a standalone Python orchestrator. Agent prompts are built into `tree_engine/tree/agents/prompts.py`; the engine does not require `.claude/`, `AGENTS.md`, or external subagent configuration files.

### 2. Features

- Standalone CLI: `tree-run`, including interactive slash commands.
- OpenAI-compatible Chat Completions API, configured through `.env`.
- PaddleOCR API v2 with a fixed job URL and default model `PaddleOCR-VL-1.6`.
- Local embedding server with `Qwen3-Embedding-4B-Q8_0` GGUF.
- Embedded Qdrant vector store at `tree_engine/.runtime/rag-store/`.
- Source Markdown is deleted after successful source embedding.
- Finished outputs are kept on disk and indexed into RAG.
- Drafts are not indexed; the Student reads the current draft directly.

### 3. Quick Start

#### 3.1 First Install (No Dependencies)

Use this path when this machine has no tree dependencies and no local embedding model yet.

Prepare:

- Python `>=3.12`; `python3.12 --version` should print a version number.
- Git; `git --version` should print a version number.
- An OpenAI-compatible Chat Completions API key. DeepSeek, OpenAI, or a self-hosted compatible gateway can be used.
- A PaddleOCR API token.
- Access to Hugging Face, or a configured proxy, because the first embedding server start downloads about 4.3 GB.

If Python or Git is missing, install [Python](https://www.python.org/downloads/) and [Git](https://git-scm.com/downloads/) first. Reopen your terminal after installation, then run the version checks above.

Clone the repository:

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
```

If you see `fatal: destination path 'engine' already exists and is not an empty directory`, an `engine` folder already exists. Stop there. Do not `cd engine` and run `git clone ... engine` again, because that creates a nested `engine/engine` install. Choose a new empty folder name such as `engine-new`, or delete/rename the old `engine` first.

All following commands must be run from the **project root**, which should contain:

```text
pyproject.toml
README.md
raw_materials/
finished_outputs/
tree_engine/
```

Check with:

```bash
pwd
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell:

```powershell
Get-Location
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

Recommended bootstrap:

```bash
./tree_engine/scripts/bootstrap.sh
```

Windows PowerShell:

```powershell
.\tree_engine\scripts\bootstrap.ps1
```

Bootstrap checks Python, OS, Apple Silicon / CUDA / CPU hints, dependency status, and CLI availability; creates `.venv`; installs dependencies; runs `tree-run setup`; and starts the embedding server in the background. The first start may download about 4.3 GB, with progress shown in the terminal.

After bootstrap finishes, the current terminal is not automatically activated into `.venv`. Run:

```bash
.venv/bin/tree-run
```

Windows PowerShell:

```powershell
.\.venv\Scripts\tree-run.exe
```

At the `TREE>` prompt, place source files in `raw_materials/`, then type:

```text
/continue
/watch
```

If you activate the virtual environment manually first, the shorter command works:

```bash
source .venv/bin/activate
tree-run
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
tree-run
```

#### 3.2 Second Install (Local Model Already Downloaded)

Use this path when you have already run tree on this machine and `Qwen3-Embedding-4B-Q8_0.gguf` is already in the Hugging Face cache.

The most reliable approach is still: **create a new `.venv` for each workspace**. This avoids missing old environment paths and prevents `tree-run` from pointing at an old checkout. A second install can reuse pip and Hugging Face caches, so it usually does not download the 4.3 GB embedding model again.

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
./tree_engine/scripts/bootstrap.sh
```

Windows PowerShell:

```powershell
git clone https://github.com/Waylon524/tree.git engine
cd engine
.\tree_engine\scripts\bootstrap.ps1
```

If you really want to reuse an old virtual environment, locate it first:

```bash
find .. -path "*/.venv/bin/activate" -print
```

Windows PowerShell:

```powershell
Get-ChildItem .. -Recurse -Filter Activate.ps1 -ErrorAction SilentlyContinue
```

Then activate the path you found and run `pip install ".[rag]"` from the new workspace, so `tree-run` points at the current checkout. Do not copy `../engine/.venv/bin/activate` unless your old workspace really is there.

### 4. Daily Use

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

Supported inputs include PDF, images, DOCX, Markdown, and TXT. The exact suffix set is defined by `tree.engine.RAW_MATERIAL_EXTENSIONS`.

Enter the interactive CLI:

```bash
source .venv/bin/activate
tree-run
```

Without activating the virtual environment:

```bash
.venv/bin/tree-run
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
tree-run
```

Common slash commands:

```text
/continue   # start or continue TREE in the background and ensure embedding is running
/watch      # refresh current progress until Ctrl+C returns to TREE>
/progress   # show one progress snapshot
/status     # show service and chapter status
/stop       # stop TREE while keeping embedding running
/quit       # stop TREE and embedding
/help       # show interactive commands
```

Traditional one-shot commands also work:

```bash
tree-run continue
tree-run watch
tree-run status
tree-run stop
tree-run quit
```

Every `/continue` or `tree-run continue` checks `raw_materials/` first:

- new or changed materials are processed through OCR -> Archivist -> source embedding
- embedding starts as soon as the first source material is produced
- the exam-writing loop starts only after all source materials are embedded
- if no new material exists, the loop resumes from `tree_engine/.runtime/pipeline-state.json`

Manual ingest:

```bash
tree-run ingest --input raw_materials/lectures --collection lectures
tree-run ingest --input raw_materials/lectures --collection lectures --no-structure
tree-run ingest --input raw_materials/lectures --collection lectures --no-index
```

### 5. API And Model Configuration

The first time you run a configuration-dependent command in a workspace, such as `tree-run continue`, `tree-run ingest`, or `tree-run doctor`, the CLI starts an interactive setup wizard if `.env` does not exist. You can also run it manually:

```bash
tree-run setup
tree-run setup --force
```

The wizard asks for:

- PaddleOCR API key.
- shared API key for the agent provider.
- LLM base URL.
- default model.
- role models for `Examiner`, `Student`, `Writer`, and `Archivist`.

The PaddleOCR job URL and PaddleOCR model are fixed and do not need input:

```text
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_MODEL=PaddleOCR-VL-1.6
```

To get a PaddleOCR API key:

1. Open the [PaddleOCR API task page](https://aistudio.baidu.com/paddleocr/task).
2. Sign in with a Baidu AI Studio / PaddlePaddle account.
3. Find the PaddleOCR-VL API call example or task creation area.
4. Copy the `TOKEN` value, or the token after `Authorization: bearer ...`.
5. When `tree-run setup` asks for `PaddleOCR API key`, paste that token.

Do not put the PaddleOCR API key in README, screenshots, or Git commits. tree writes it to the local `.env`, which is ignored by Git. When entering API keys, the terminal does not display any characters. This is normal hidden input, like typing a password. After setup, run `tree-run models` to verify that keys are `set` / `not set`; real secrets are never printed.

Model names must exactly match the names supported by your provider. Do not include spaces, terminal color fragments, or extra characters. For example, DeepSeek currently expects `deepseek-v4-pro` or `deepseek-v4-flash`, not `deepseek-v4-pro[1m]`. Fix model settings with:

```bash
tree-run models \
  --base-url https://api.deepseek.com/v1 \
  --examiner deepseek-v4-pro \
  --student deepseek-v4-flash \
  --writer deepseek-v4-flash \
  --archivist deepseek-v4-flash
```

Update model/provider settings later with:

```bash
tree-run models
tree-run models --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash
tree-run models --examiner deepseek-v4-flash --student deepseek-v4-flash
tree-run models --api-key
tree-run models --paddleocr-key
```

If `tree-run setup` still asks for `PaddleOCR job API URL` or `PaddleOCR model`, your installed checkout is old. From the project root, run:

```bash
git pull
source .venv/bin/activate
pip install ".[rag]"
```

Windows PowerShell:

```powershell
git pull
.\.venv\Scripts\Activate.ps1
pip install ".[rag]"
```

The generated `.env` looks roughly like this. The PaddleOCR URL and model are written by the CLI as fixed values:

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

The DeepSeek URL and model above are examples only. Any OpenAI-compatible Chat Completions provider can be used. Role-specific keys, base URLs, and models are also supported through `EXAMINER_*`, `STUDENT_*`, `WRITER_*`, and `ARCHIVIST_*`.

### 6. CLI Commands

Interactive mode:

```bash
tree-run
```

At the `TREE>` prompt:

```text
/continue
/status
/progress
/watch
/stop
/quit
/logs --tail 20
/materials
/doctor
/models
/rag status
/help
/exit
```

Traditional one-shot commands are still available:

```bash
tree-run --help
tree-run continue
tree-run stop
tree-run quit
tree-run run
tree-run resume
tree-run status
tree-run status --verbose
tree-run progress
tree-run watch
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
| `run` | Start the full pipeline in the foreground, useful for debugging |
| `continue` | Start or continue TREE in the background and ensure embedding is running |
| `stop` | Stop TREE while keeping the embedding server running |
| `quit` | Stop TREE and the embedding server |
| `resume` | Continue from existing state in the foreground, useful for debugging |
| `status` | Show service and chapter progress |
| `progress` | Show a dashboard snapshot of services, ingest, chapters, and recent trace |
| `watch` | Refresh current progress until `Ctrl+C` returns to the prompt |
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

### 7. Runtime Design

#### 7.1 Agent Workflow

| Role | Prompt | Purpose |
| --- | --- | --- |
| Examiner | `EXAMINER_PROMPT` | Finds knowledge points, composes exams, audits answers |
| Student | `STUDENT_PROMPT` | Zero-baseline learner using only learned materials and current draft |
| Writer | `WRITER_PROMPT` | Creates or optimizes drafts from abstract bottleneck reports |
| Archivist | `ARCHIVIST_PROMPT` | Cleans PaddleOCR output into normalized Markdown |

#### 7.2 RAG Strategy

- Source materials are deleted from `tree_engine/.runtime/source_materials/` after indexing.
- Finished outputs remain in `finished_outputs/` and are indexed.
- Drafts are not indexed; the Student reads the current draft directly.
- Examiner exam assembly uses source RAG and finished-output RAG.
- Student answers use RAG retrieval over already learned finished outputs and direct reading of the current draft.
- Retrieval uses semantic chunks of about 1500-3000 tokens plus adjacent chunk expansion.

Chunk budgets:

```python
MAX_TOKENS = {
    "def": 2000,
    "proof": 3000,
    "example": 2400,
    "narrative": 1500,
}
```

#### 7.3 PaddleOCR-VL-1.6

Default model:

```text
PADDLEOCR_MODEL=PaddleOCR-VL-1.6
```

OCR jobs use:

```python
optionalPayload = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}
```

OCR uploads submit one file every 5 seconds by default; upload and polling can run concurrently; Archivist can process multiple files concurrently; embedding is serial by default.

#### 7.4 Local Embedding Model

tree uses `Qwen3-Embedding-4B-Q8_0.gguf` from `Qwen/Qwen3-Embedding-4B-GGUF` by default. The model is downloaded automatically on first embedding server start. The file is about 4.3 GB and stays in the local Hugging Face cache for later workspaces.

`pip install ".[rag]"` already installs the Python dependencies required by the embedding server. `setup-embedding.sh` is mainly for rebuilding or forcing a Metal/CUDA/CPU `llama-cpp-python` variant on macOS / Linux.

```bash
./tree_engine/scripts/setup-embedding.sh
./tree_engine/scripts/setup-embedding.sh --device metal
./tree_engine/scripts/setup-embedding.sh --device cpu
./tree_engine/scripts/setup-embedding.sh --device cuda
```

Windows PowerShell users usually do not need to run `setup-embedding.sh`; it is a macOS / Linux shell script.

Bootstrap and `/continue` manage the embedding server in the background. Manual foreground startup is mainly for debugging:

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell:

```powershell
tree_engine\scripts\start-embed-server.bat
```

Foreground startup occupies the current terminal. To run `tree-run setup`, `tree-run continue`, or `tree-run ingest`, open another terminal tab, return to the project root, and activate the same virtual environment.

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

### 8. Repository Layout

```text
engine/
├── README.md
├── pyproject.toml
├── raw_materials/          # User uploads; directory is kept, contents are ignored
├── finished_outputs/       # Final textbooks; directory is kept, contents are ignored
└── tree_engine/            # Engine source, scripts, RAG, ingest, and internal runtime
    ├── tree/               # Main engine Python package
    ├── rag/                # Local embedding server and chunker
    ├── ingest/             # Low-level OCR/structuring ingest modules
    ├── scripts/            # Setup and runtime helper scripts
    └── .runtime/           # Auto-created state, traces, drafts, sources, vector store
```

Runtime paths are created automatically and ignored by Git. `raw_materials/` is kept as an empty upload directory, and `finished_outputs/` is kept for final textbooks. Real lecture files, exercises, handouts, and generated outputs inside those folders are not committed.

```text
raw_materials/
finished_outputs/
tree_engine/.runtime/   # source_materials, drafts, pipeline-state, trace, rag-store, services
```

### 9. Verification And Troubleshooting

This repository no longer ships built-in sample data or a unit test directory. For code changes, run at least:

```bash
ruff check tree_engine/tree tree_engine/rag tree_engine/ingest
python -m compileall tree_engine/tree tree_engine/rag tree_engine/ingest
```

For end-to-end verification, place real materials in `raw_materials/`, then run:

```bash
tree-run continue
tree-run watch
```

Common issues:

**`Source materials exist but RAG indexer is unavailable`**

The embedding server is not running or RAG dependencies are missing.

```bash
pip install ".[rag]"
tree-run continue
```

Manual foreground debugging:

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell:

```powershell
pip install ".[rag]"
tree_engine\scripts\start-embed-server.bat
```

**`tree-run` is not found**

Bootstrap cannot activate the virtual environment in the current terminal. Run:

```bash
.venv/bin/tree-run
```

Or activate first:

```bash
source .venv/bin/activate
tree-run
```

Windows PowerShell:

```powershell
.\.venv\Scripts\tree-run.exe
.\.venv\Scripts\Activate.ps1
tree-run
```

**`tree-run` cannot import the local package**

If you see `ModuleNotFoundError: No module named 'tree'`, an old editable install probably did not bind the source path correctly. From the project root, reinstall in non-editable mode:

```bash
source .venv/bin/activate
pip install --force-reinstall ".[rag]"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install --force-reinstall ".[rag]"
```

Source-checkout fallback:

```bash
PYTHONPATH=tree_engine python -m tree.cli --help
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "tree_engine"
python -m tree.cli --help
```

**Clone went to the wrong location or created `engine/engine`**

If `git clone ... engine` says the destination already exists, do not `cd engine` and clone again inside it. Bootstrap now blocks `.Trash` paths and nested checkouts. Choose a new empty folder name, or delete/rename the old folder and clone again.

**GitHub repository name**

Remote URL:

```text
https://github.com/Waylon524/tree.git
```

The GitHub repository is displayed as `tree`.

### 10. License

MIT. See [LICENSE](LICENSE).
