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

当前实现是独立 Python 编排器，agent prompts 内置在 `tree_engine/tree/agents/prompts.py` 中，无需外部 agent 配置文件。

### 当前特性

- 独立 CLI：`tree-run`
- OpenAI-compatible Chat Completions API，供应商由 `.env` 配置
- PaddleOCR API v2，默认模型 `PaddleOCR-VL-1.6`
- 本地 embedding server：`Qwen3-Embedding-4B-Q8_0` GGUF
- embedded Qdrant 向量库，默认目录 `tree_engine/.runtime/rag-store/`
- source materials 入库后删除中间 Markdown
- finished outputs 保留原文件并写入 RAG
- draft 不写入 RAG，Student 直接读取当前 draft 全文

### 项目结构

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
tree_engine/.runtime/   # source_materials、drafts、pipeline-state、trace、rag-store
```

### 安装

安装分两种情况：

- **首次安装**：这台电脑从未安装过 tree 的依赖，也没有下载过本地 embedding 模型。
- **二次安装**：这台电脑已经下载过本地 embedding 模型，只是换了一个新的工作区或重新 clone 仓库。

#### 首次安装（无依赖）

开始前需要准备：

- Python `>=3.12`。终端中运行 `python3.12 --version` 能看到版本号即可。
- Git。终端中运行 `git --version` 能看到版本号即可。
- 一个 OpenAI-compatible Chat Completions API key。DeepSeek、OpenAI 或自托管兼容网关都可以。
- PaddleOCR API token。
- 能访问 Hugging Face 或已配置代理，因为首次启动 embedding server 会下载本地 embedding 模型。

如果本机还没有 Python 或 Git，先安装 [Python](https://www.python.org/downloads/) 和 [Git](https://git-scm.com/downloads)。安装后重新打开终端，再运行上面的版本检查命令。

1. 克隆仓库：

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
```

如果看到 `fatal: destination path 'engine' already exists and is not an empty directory`，说明当前目录已经有一个叫 `engine` 的文件夹。此时请先停下来，不要继续 `cd engine` 后再执行一次 `git clone ... engine`，否则会变成 `engine/engine` 嵌套安装。请选择一个新的空目录名，例如 `engine-new`，或先删除/重命名旧的 `engine`。

后续命令都必须在这个**项目根目录**执行。项目根目录里应该能看到 `pyproject.toml`、`README.md`、`raw_materials/`、`finished_outputs/` 和 `tree_engine/`。不要再执行 `cd tree` 进入里面的源码包目录。

可以用下面的命令检查：

macOS / Linux：

```bash
pwd
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell：

```powershell
Get-Location
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

2. 推荐使用 bootstrap 脚本自动检查设备配置、创建 `.venv`、安装依赖，并进入 `tree-run setup`：

macOS / Linux：

```bash
./tree_engine/scripts/bootstrap.sh
```

Windows PowerShell：

```powershell
.\tree_engine\scripts\bootstrap.ps1
```

这个脚本会检查 Python 版本、系统类型、Apple Silicon / CUDA / CPU 设备提示、依赖安装状态和 CLI 可用性。它会在 setup 完成后自动把 embedding server 启动到后台，并在当前终端显示启动日志。首次启动可能下载约 4.3 GB 模型，下载和加载过程会显示在终端中。

bootstrap 结束后，当前终端不会自动进入 `.venv`。因此可以直接使用虚拟环境里的完整命令进入交互模式：

macOS / Linux：

```bash
.venv/bin/tree-run
```

Windows PowerShell：

```powershell
.\.venv\Scripts\tree-run.exe
```

进入 `TREE>` 后输入 `/continue` 启动流水线，输入 `/status`、`/stop`、`/quit` 管理运行状态。如果先手动激活虚拟环境，之后也可以继续使用短命令 `tree-run`。

下面是手动安装步骤，适合需要逐步排查环境问题时使用。

3. 创建并进入 Python 虚拟环境：

macOS / Linux：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

4. 升级基础安装工具：

```bash
pip install -U pip
```

5. 安装 tree 引擎和 RAG/embedding 依赖：

```bash
pip install ".[rag]"
```

这个命令会把当前仓库安装进虚拟环境，并注册 `tree-run` 命令。普通用户建议使用这种非 editable 安装，入口脚本更稳定。

如果你要开发或调试源码，可以改用 editable 模式安装开发依赖：

```bash
pip install -e ".[rag,dev]"
```

6. 确认 CLI 可用：

```bash
tree-run --help
```

如果 `tree-run` 暂时不可用，说明当前 shell 还没有识别虚拟环境中的命令。先确认已经激活虚拟环境，也可以临时使用源码入口。

macOS / Linux：

```bash
PYTHONPATH=tree_engine python -m tree.cli --help
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "tree_engine"
python -m tree.cli --help
```

7. 确认本地 embedding 依赖可用：

```bash
python -c "import llama_cpp, huggingface_hub, fastapi, uvicorn; print('embedding deps ok')"
```

如果这条命令通过，可以直接进入下一步。

如果你在 macOS / Linux 上需要重新编译 GPU/Metal/CUDA 版本的 `llama-cpp-python`，可以运行：

```bash
./tree_engine/scripts/setup-embedding.sh
```

Apple Silicon 推荐显式使用 Metal：

```bash
./tree_engine/scripts/setup-embedding.sh --device metal
```

CPU-only：

```bash
./tree_engine/scripts/setup-embedding.sh --device cpu
```

NVIDIA CUDA：

```bash
./tree_engine/scripts/setup-embedding.sh --device cuda
```

Windows PowerShell 用户通常不需要运行 `setup-embedding.sh`；它是 macOS / Linux shell 脚本。Windows 已在 `pip install ".[rag]"` 中安装 embedding 依赖。

8. 启动 embedding server。

macOS / Linux：

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell：

```powershell
tree_engine\scripts\start-embed-server.bat
```

首次启动会自动下载 `Qwen3-Embedding-4B-Q8_0.gguf`，约 4.3 GB。下载完成后模型会留在本机 Hugging Face 缓存中，之后换工作区通常不需要重新下载。

这个命令会一直占用当前终端运行服务。看到 `Model loaded` 或服务日志后，不要在这个终端继续输入 `tree-run setup` 或 `tree-run continue`。保持它开着，然后新开一个终端标签页。

9. 在新终端标签页中回到项目根目录，并激活同一个 `.venv`：

macOS / Linux：

```bash
cd /path/to/engine
source .venv/bin/activate
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell：

```powershell
cd C:\path\to\engine
.\.venv\Scripts\Activate.ps1
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

把 `/path/to/engine` 或 `C:\path\to\engine` 替换成你的实际仓库路径。如果你不确定路径，在启动 embedding server 的旧终端里运行 `pwd`（macOS / Linux）或 `Get-Location`（Windows PowerShell）复制完整路径。确认当前目录能看到 `pyproject.toml` 后，再继续。

10. 配置 API key 和模型。第一次运行 `tree-run continue` 时会自动弹出配置向导，也可以手动运行：

```bash
tree-run setup
```

11. 把课件、习题或讲义放入 `raw_materials/`，然后运行：

```bash
tree-run continue
```

#### 二次安装（本机已下载过本地模型）

适用于：你已经在这台电脑上成功跑过 tree，Hugging Face 缓存里已有 `Qwen3-Embedding-4B-Q8_0.gguf`，现在只是换一个新工作区。

最稳妥的做法是：**每个工作区创建自己的 `.venv`**。这样不会因为找不到旧虚拟环境目录而卡住，也不会让 `tree-run` 指向旧 checkout。二次安装仍然会安装 Python 包到新 `.venv`，但通常会复用 pip 缓存；重点是不用重新下载 4.3 GB 的 embedding 模型。

1. 克隆新工作区：

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
```

如果看到 `fatal: destination path 'engine' already exists and is not an empty directory`，说明当前目录已经有一个叫 `engine` 的文件夹。此时请先停下来，不要继续 `cd engine` 后再执行一次 `git clone ... engine`，否则会变成 `engine/engine` 嵌套安装。请选择一个新的空目录名，例如 `engine-new`，或先删除/重命名旧的 `engine`。

后续命令都必须在 `engine` 这个**项目根目录**执行。项目根目录里应该能看到 `pyproject.toml`、`README.md`、`raw_materials/`、`finished_outputs/` 和 `tree_engine/`。不要再执行 `cd tree`；`tree_engine/tree/` 才是源码包目录，不是项目根目录。

可以用下面的命令检查：

macOS / Linux：

```bash
pwd
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell：

```powershell
Get-Location
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

2. 推荐直接运行 bootstrap。它会在新工作区创建 `.venv`、复用本机 pip/Hugging Face 缓存、安装依赖，并进入 `tree-run setup`：

macOS / Linux：

```bash
./tree_engine/scripts/bootstrap.sh
```

Windows PowerShell：

```powershell
.\tree_engine\scripts\bootstrap.ps1
```

bootstrap 结束后，当前终端不会自动进入 `.venv`。可以直接运行 `.venv/bin/tree-run`（macOS / Linux）或 `.\.venv\Scripts\tree-run.exe`（Windows PowerShell）进入交互模式，然后输入 `/continue`。如果先手动激活虚拟环境，也可以使用短命令 `tree-run`。

下面是手动安装步骤。

3. 在新工作区创建并激活虚拟环境：

macOS / Linux：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

4. 安装 tree 和 RAG/embedding 依赖：

```bash
pip install -U pip
pip install ".[rag]"
```

这一步通常比首次安装快，因为 pip 会复用本机缓存。它不会重新下载 embedding 模型。

5. 确认本地 embedding 依赖可用：

```bash
python -c "import llama_cpp, huggingface_hub, fastapi, uvicorn; print('embedding deps ok')"
```

如果这条命令失败，或者你需要重新编译 GPU/Metal/CUDA 版本的 `llama-cpp-python`，再运行：

```bash
./tree_engine/scripts/setup-embedding.sh
```

Apple Silicon 可用：

```bash
./tree_engine/scripts/setup-embedding.sh --device metal
```

Windows PowerShell 用户通常不需要运行 `setup-embedding.sh`；它是 macOS / Linux shell 脚本。

6. 启动 embedding server。

macOS / Linux：

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell：

```powershell
tree_engine\scripts\start-embed-server.bat
```

如果本地模型缓存还在，这一步会直接复用缓存，不会重新下载 4.3 GB 模型。

这个命令会一直占用当前终端运行服务。看到 `Model loaded` 或服务日志后，不要在这个终端继续输入 `tree-run setup` 或 `tree-run continue`。保持它开着，然后新开一个终端标签页。

7. 在新终端标签页中回到 `engine` 项目根目录，并激活同一个 `.venv`：

macOS / Linux：

```bash
cd /path/to/engine
source .venv/bin/activate
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell：

```powershell
cd C:\path\to\engine
.\.venv\Scripts\Activate.ps1
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

把 `/path/to/engine` 或 `C:\path\to\engine` 替换成你的实际工作区路径。如果你不确定路径，在启动 embedding server 的旧终端里运行 `pwd`（macOS / Linux）或 `Get-Location`（Windows PowerShell）复制完整路径。确认当前目录能看到 `pyproject.toml` 后，再继续。

8. 每个工作区都需要自己的 `.env`。运行配置向导：

```bash
tree-run setup
```

9. 把课件、习题或讲义放入 `raw_materials/`，然后运行：

```bash
tree-run continue
```

如果你确实想复用旧虚拟环境，先找到旧环境的真实路径：

```bash
find .. -path "*/.venv/bin/activate" -print
```

然后激活找到的路径，并在新工作区重新执行 `pip install ".[rag]"`，让 `tree-run` 指向当前 checkout。不要照抄 `../engine/.venv/bin/activate`，除非你的旧工作区确实在这个位置。

Windows PowerShell 可用：

```powershell
Get-ChildItem .. -Recurse -Filter Activate.ps1 -ErrorAction SilentlyContinue
```

### 本地 Embedding 模型

tree 默认使用 `Qwen/Qwen3-Embedding-4B-GGUF` 中的 `Qwen3-Embedding-4B-Q8_0.gguf`。首次启动 embedding server 时会自动下载模型，文件大小约 4.3 GB。

`pip install ".[rag]"` 已经安装了 embedding server 所需的 Python 依赖。下面的 `setup-embedding.sh` 主要用于 macOS / Linux 上重新编译或强制选择 Metal/CUDA/CPU 版本的 `llama-cpp-python`。

macOS / Linux：

```bash
./tree_engine/scripts/setup-embedding.sh
```

Apple Silicon 推荐：

```bash
./tree_engine/scripts/setup-embedding.sh --device metal
```

CPU-only：

```bash
./tree_engine/scripts/setup-embedding.sh --device cpu
```

NVIDIA CUDA：

```bash
./tree_engine/scripts/setup-embedding.sh --device cuda
```

Windows PowerShell 用户通常不需要运行 `setup-embedding.sh`；如果 `pip install ".[rag]"` 已成功，可以直接启动服务。

启动 embedding server。

macOS / Linux：

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell：

```powershell
tree_engine\scripts\start-embed-server.bat
```

这个终端会被 embedding server 占用。运行 `tree-run setup`、`tree-run continue`、`tree-run ingest` 等命令时，请新开一个终端标签页，回到项目根目录，重新激活虚拟环境后再运行：macOS / Linux 使用 `source .venv/bin/activate`，Windows PowerShell 使用 `.\.venv\Scripts\Activate.ps1`。

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

第一次在某个工作区中运行需要配置的命令时，例如 `tree-run continue`、`tree-run ingest` 或 `tree-run doctor`，如果当前目录没有 `.env`，CLI 会自动启动交互式配置向导。向导会在命令行中要求输入：

- PaddleOCR API key
- 子智能体共享 API key
- LLM base URL
- 默认模型
- `Examiner`、`Student`、`Writer`、`Archivist` 四个角色的模型

PaddleOCR job URL 和 PaddleOCR model 是固定值，不需要填写。当前固定为 `https://paddleocr.aistudio-app.com/api/v2/ocr/jobs` 和 `PaddleOCR-VL-1.6`。

获取 PaddleOCR API key：

1. 打开 [PaddleOCR API 任务页](https://aistudio.baidu.com/paddleocr/task)。
2. 登录百度 AI Studio / 飞桨账号。
3. 在页面中找到 PaddleOCR-VL 的 API 调用示例或任务创建区域。
4. 复制示例里的 `TOKEN` 或 `Authorization: bearer ...` 后面的 token。
5. 运行 `tree-run setup` 时，把这个 token 粘贴到 `PaddleOCR API key` 输入项。

不要把 PaddleOCR API key 写进 README、截图或提交到 Git。T.R.E.E. 会把它写入本地 `.env`，该文件已被 `.gitignore` 排除。

输入 API key 时终端不会显示任何字符，这是正常的隐藏输入，类似输入密码。直接粘贴或输入完整 key，然后按 Enter 即可。配置完成后可以运行 `tree-run models`，它只会显示 key 是 `set` / `not set`，不会打印真实密钥。

模型名必须完全匹配供应商支持的名称，不要带空格、颜色控制残留或多余字符。例如 DeepSeek 当前应填写 `deepseek-v4-pro` 或 `deepseek-v4-flash`，不要填写 `deepseek-v4-pro[1m]`。如果填错了，可以直接修正：

```bash
tree-run models \
  --base-url https://api.deepseek.com/v1 \
  --examiner deepseek-v4-pro \
  --student deepseek-v4-flash \
  --writer deepseek-v4-flash \
  --archivist deepseek-v4-flash
```

如果 `tree-run setup` 仍然询问 `PaddleOCR job API URL` 或 `PaddleOCR model`，说明你当前安装的是旧版本。请在项目根目录运行：

macOS / Linux：

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

启动流水线。推荐进入交互模式：

macOS / Linux：

```bash
source .venv/bin/activate
tree-run
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
tree-run
```

在 `TREE>` 提示符中输入：

```text
/continue
```

也可以不进入交互模式，直接运行 `tree-run continue`。每次 `/continue` 或 `tree-run continue` 都会先检查 `raw_materials/`：

- 有新增或变更资料：先执行 OCR -> Archivist -> source embedding。
- 第一个 source material 生成后即可开始串行 embedding。
- 所有 source materials embedding 完成后，才进入考试-写作循环。
- 没有新资料：直接从 `tree_engine/.runtime/pipeline-state.json` 恢复循环。

断点恢复：

```bash
tree-run continue
```

手动摄入某个文件或目录：

```bash
tree-run ingest --input raw_materials/课件 --collection 课件
tree-run ingest --input raw_materials/课件 --collection 课件 --no-structure
tree-run ingest --input raw_materials/课件 --collection 课件 --no-index
```

### CLI 命令

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

命令说明：

| 命令 | 作用 |
| --- | --- |
| `run` | 前台启动完整流水线，适合调试 |
| `continue` | 后台启动或继续 TREE，自动确保 embedding server 运行 |
| `stop` | 停止 TREE，保留 embedding server |
| `quit` | 停止 TREE 和 embedding server |
| `resume` | 前台从现有状态继续，适合调试 |
| `status` | 查看章节进度 |
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
ruff check tree_engine/tree tree_engine/rag tree_engine/ingest
python -m compileall tree_engine/tree tree_engine/rag tree_engine/ingest
```

需要端到端验证时，将真实资料放入 `raw_materials/`，启动 embedding server，然后运行：

```bash
tree-run continue
```

### 常见问题

**`Source materials exist but RAG indexer is unavailable`**

说明 embedding server 未启动或 RAG 依赖未安装。

```bash
pip install ".[rag]"
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell：

```powershell
pip install ".[rag]"
tree_engine\scripts\start-embed-server.bat
```

**`tree-run` 无法导入本地包**

如果看到 `ModuleNotFoundError: No module named 'tree'`，通常是旧的 editable 安装没有正确绑定源码路径。请在项目根目录重新用非 editable 模式安装：

macOS / Linux：

```bash
source .venv/bin/activate
pip install --force-reinstall ".[rag]"
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
pip install --force-reinstall ".[rag]"
```

源码调试时也可临时使用。

macOS / Linux：

```bash
PYTHONPATH=tree_engine python -m tree.cli --help
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "tree_engine"
python -m tree.cli --help
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

The current runtime is a standalone Python orchestrator. Agent prompts are built into `tree_engine/tree/agents/prompts.py`; the engine does not require external agent configuration files.

### Features

- Standalone CLI: `tree-run`
- OpenAI-compatible Chat Completions API, configured through `.env`
- PaddleOCR API v2, default model `PaddleOCR-VL-1.6`
- Local embedding server with `Qwen3-Embedding-4B-Q8_0` GGUF
- Embedded Qdrant vector store at `tree_engine/.runtime/rag-store/`
- Source Markdown is deleted after successful source embedding
- Finished outputs are kept on disk and indexed into RAG
- Drafts are not indexed; the Student reads the current draft directly

### Repository Layout

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
tree_engine/.runtime/
```

### Installation

There are two installation paths:

- **First install**: this machine has no tree dependencies and no local embedding model yet.
- **Second install**: this machine has already downloaded the local embedding model, and you are only creating another workspace or cloning the repository again.

#### First Install (No Dependencies)

Before starting, prepare:

- Python `>=3.12`. `python3.12 --version` should print a version number.
- Git. `git --version` should print a version number.
- An OpenAI-compatible Chat Completions API key. DeepSeek, OpenAI, or a self-hosted compatible gateway can be used.
- A PaddleOCR API token.
- Access to Hugging Face, or a configured proxy, because the local embedding model is downloaded on first start.

If Python or Git is missing, install [Python](https://www.python.org/downloads/) and [Git](https://git-scm.com/downloads/) first. Reopen your terminal after installation, then run the version checks above.

1. Clone the repository:

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
```

If you see `fatal: destination path 'engine' already exists and is not an empty directory`, an `engine` folder already exists in the current directory. Stop there. Do not `cd engine` and run `git clone ... engine` again, because that creates a nested `engine/engine` install. Choose a new empty folder name such as `engine-new`, or delete/rename the old `engine` first.

All following commands must be run from this **project root**. The project root should contain `pyproject.toml`, `README.md`, `raw_materials/`, `finished_outputs/`, and `tree_engine/`. Do not run another `cd tree`; `tree_engine/tree/` is the Python source package, not the project root.

Check with:

macOS / Linux:

```bash
pwd
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell:

```powershell
Get-Location
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

2. Recommended: run the bootstrap script to check the device profile, create `.venv`, install dependencies, and start `tree-run setup`:

macOS / Linux:

```bash
./tree_engine/scripts/bootstrap.sh
```

Windows PowerShell:

```powershell
.\tree_engine\scripts\bootstrap.ps1
```

The script checks Python, OS, Apple Silicon / CUDA / CPU hints, dependency status, and CLI availability. After setup, it starts the embedding server in the background and streams the startup log in the current terminal. The first start may download about 4.3 GB, and download/loading progress is shown while you wait.

After bootstrap finishes, the current terminal is not automatically activated into `.venv`. You can run the virtual-environment command directly to enter interactive mode:

macOS / Linux:

```bash
.venv/bin/tree-run
```

Windows PowerShell:

```powershell
.\.venv\Scripts\tree-run.exe
```

At the `TREE>` prompt, type `/continue` to start the pipeline, or use `/status`, `/stop`, and `/quit` to manage it. If you activate the virtual environment manually first, the shorter `tree-run` command works too.

The manual steps below are useful when you need to debug the environment one step at a time.

3. Create and activate a Python virtual environment:

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

4. Upgrade the base installer:

```bash
pip install -U pip
```

5. Install the tree engine and RAG/embedding dependencies:

```bash
pip install ".[rag]"
```

This installs the current checkout into the virtual environment and registers the `tree-run` command. For regular users, this non-editable install is more stable for console scripts.

For source development and linting, use editable mode with the development extras:

```bash
pip install -e ".[rag,dev]"
```

6. Confirm that the CLI is available:

```bash
tree-run --help
```

If `tree-run` is not found, make sure the virtual environment is active. You can also use this source-checkout fallback.

macOS / Linux:

```bash
PYTHONPATH=tree_engine python -m tree.cli --help
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "tree_engine"
python -m tree.cli --help
```

7. Confirm that local embedding dependencies are available:

```bash
python -c "import llama_cpp, huggingface_hub, fastapi, uvicorn; print('embedding deps ok')"
```

If this command succeeds, continue to the next step.

If you need to rebuild the GPU/Metal/CUDA version of `llama-cpp-python` on macOS / Linux, run:

```bash
./tree_engine/scripts/setup-embedding.sh
```

Apple Silicon:

```bash
./tree_engine/scripts/setup-embedding.sh --device metal
```

CPU-only:

```bash
./tree_engine/scripts/setup-embedding.sh --device cpu
```

NVIDIA CUDA:

```bash
./tree_engine/scripts/setup-embedding.sh --device cuda
```

Windows PowerShell users usually do not need to run `setup-embedding.sh`; it is a macOS / Linux shell script. Windows embedding dependencies were installed by `pip install ".[rag]"`.

8. Start the embedding server.

macOS / Linux:

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell:

```powershell
tree_engine\scripts\start-embed-server.bat
```

The first start downloads `Qwen3-Embedding-4B-Q8_0.gguf`, about 4.3 GB. After that, the model stays in the local Hugging Face cache, so a second workspace usually does not download it again.

This command keeps running and occupies the current terminal. After you see `Model loaded` or server logs, do not type `tree-run setup` or `tree-run continue` in that same terminal. Keep it open and start a new terminal tab.

9. In the new terminal tab, return to the project root and activate the same `.venv`:

macOS / Linux:

```bash
cd /path/to/engine
source .venv/bin/activate
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell:

```powershell
cd C:\path\to\engine
.\.venv\Scripts\Activate.ps1
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

Replace `/path/to/engine` or `C:\path\to\engine` with your real repository path. If you are not sure, run `pwd` in the old macOS / Linux terminal or `Get-Location` in the old Windows PowerShell tab and copy the full path. Continue only after `pyproject.toml` is visible.

10. Configure API keys and model names. The first `tree-run continue` starts the setup wizard automatically, or you can run it manually:

```bash
tree-run setup
```

11. Put lectures, exercises, or handouts into `raw_materials/`, then run:

```bash
tree-run continue
```

#### Second Install (Local Model Already Downloaded)

Use this path when you have already run tree successfully on this machine and `Qwen3-Embedding-4B-Q8_0.gguf` is already in the Hugging Face cache.

The most reliable approach is: **create a new `.venv` for each workspace**. This avoids missing old environment paths and prevents `tree-run` from pointing at an old checkout. A second install still installs Python packages into the new `.venv`, but it usually reuses pip cache; the important part is that it does not download the 4.3 GB embedding model again.

1. Clone a new workspace:

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
```

If you see `fatal: destination path 'engine' already exists and is not an empty directory`, an `engine` folder already exists in the current directory. Stop there. Do not `cd engine` and run `git clone ... engine` again, because that creates a nested `engine/engine` install. Choose a new empty folder name such as `engine-new`, or delete/rename the old `engine` first.

All following commands must be run from the `engine` **project root**. The project root should contain `pyproject.toml`, `README.md`, `raw_materials/`, `finished_outputs/`, and `tree_engine/`. Do not run another `cd tree`; `tree_engine/tree/` is the Python source package, not the project root.

Check with:

macOS / Linux:

```bash
pwd
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell:

```powershell
Get-Location
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

2. Recommended: run bootstrap in the new workspace. It creates `.venv`, reuses local pip/Hugging Face caches, installs dependencies, and starts `tree-run setup`:

macOS / Linux:

```bash
./tree_engine/scripts/bootstrap.sh
```

Windows PowerShell:

```powershell
.\tree_engine\scripts\bootstrap.ps1
```

After bootstrap finishes, the current terminal is not automatically activated into `.venv`. Run `.venv/bin/tree-run` on macOS / Linux or `.\.venv\Scripts\tree-run.exe` on Windows PowerShell to enter interactive mode, then type `/continue`. If you activate the virtual environment manually first, the shorter `tree-run` command works too.

The manual steps are below.

3. Create and activate a virtual environment in the new workspace:

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

4. Install tree and RAG/embedding dependencies:

```bash
pip install -U pip
pip install ".[rag]"
```

This is usually faster than the first install because pip can reuse the local package cache. It does not download the embedding model again.

5. Confirm that local embedding dependencies are present:

```bash
python -c "import llama_cpp, huggingface_hub, fastapi, uvicorn; print('embedding deps ok')"
```

If this command fails, or if you need to rebuild the GPU/Metal/CUDA version of `llama-cpp-python`, run:

```bash
./tree_engine/scripts/setup-embedding.sh
```

Apple Silicon:

```bash
./tree_engine/scripts/setup-embedding.sh --device metal
```

Windows PowerShell users usually do not need to run `setup-embedding.sh`; it is a macOS / Linux shell script.

6. Start the embedding server.

macOS / Linux:

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell:

```powershell
tree_engine\scripts\start-embed-server.bat
```

If the local model cache is still present, this reuses it without downloading the 4.3 GB model again.

This command keeps running and occupies the current terminal. After you see `Model loaded` or server logs, do not type `tree-run setup` or `tree-run continue` in that same terminal. Keep it open and start a new terminal tab.

7. In the new terminal tab, return to the `engine` project root and activate the same `.venv`:

macOS / Linux:

```bash
cd /path/to/engine
source .venv/bin/activate
ls pyproject.toml README.md tree_engine raw_materials finished_outputs
```

Windows PowerShell:

```powershell
cd C:\path\to\engine
.\.venv\Scripts\Activate.ps1
Get-ChildItem pyproject.toml, README.md, tree_engine, raw_materials, finished_outputs
```

Replace `/path/to/engine` or `C:\path\to\engine` with your real workspace path. If you are not sure, run `pwd` in the old macOS / Linux terminal or `Get-Location` in the old Windows PowerShell tab and copy the full path. Continue only after `pyproject.toml` is visible.

8. Each workspace needs its own `.env`. Run the setup wizard:

```bash
tree-run setup
```

9. Put lectures, exercises, or handouts into `raw_materials/`, then run:

```bash
tree-run continue
```

If you really want to reuse an old virtual environment, first locate its real path:

```bash
find .. -path "*/.venv/bin/activate" -print
```

Then activate the path you found and run `pip install ".[rag]"` from the new workspace, so `tree-run` points at the current checkout. Do not copy `../engine/.venv/bin/activate` unless your old workspace really is there.

On Windows PowerShell:

```powershell
Get-ChildItem .. -Recurse -Filter Activate.ps1 -ErrorAction SilentlyContinue
```

### Local Embedding Model

tree uses `Qwen3-Embedding-4B-Q8_0.gguf` from `Qwen/Qwen3-Embedding-4B-GGUF` by default. The model is downloaded automatically on the first embedding server start. The file is about 4.3 GB.

`pip install ".[rag]"` already installs the Python dependencies required by the embedding server. The `setup-embedding.sh` script below is mainly for rebuilding or forcing a Metal/CUDA/CPU `llama-cpp-python` variant on macOS / Linux.

macOS / Linux:

```bash
./tree_engine/scripts/setup-embedding.sh
```

Apple Silicon:

```bash
./tree_engine/scripts/setup-embedding.sh --device metal
```

CPU-only:

```bash
./tree_engine/scripts/setup-embedding.sh --device cpu
```

NVIDIA CUDA:

```bash
./tree_engine/scripts/setup-embedding.sh --device cuda
```

Windows PowerShell users usually do not need to run `setup-embedding.sh`; if `pip install ".[rag]"` succeeded, start the server directly.

Start the embedding server.

macOS / Linux:

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell:

```powershell
tree_engine\scripts\start-embed-server.bat
```

This terminal is now occupied by the embedding server. To run `tree-run setup`, `tree-run continue`, or `tree-run ingest`, open a new terminal tab, return to the project root, and activate the virtual environment again first: use `source .venv/bin/activate` on macOS / Linux or `.\.venv\Scripts\Activate.ps1` on Windows PowerShell.

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

The first time you run a configuration-dependent command in a workspace, such as `tree-run continue`, `tree-run ingest`, or `tree-run doctor`, the CLI starts an interactive setup wizard if `.env` does not exist. The wizard asks for:

- PaddleOCR API key
- shared API key for the agent provider
- LLM base URL
- default model
- role models for `Examiner`, `Student`, `Writer`, and `Archivist`

The PaddleOCR job URL and PaddleOCR model are fixed and do not need input. They are currently fixed to `https://paddleocr.aistudio-app.com/api/v2/ocr/jobs` and `PaddleOCR-VL-1.6`.

To get a PaddleOCR API key:

1. Open the [PaddleOCR API task page](https://aistudio.baidu.com/paddleocr/task).
2. Sign in with a Baidu AI Studio / PaddlePaddle account.
3. Find the PaddleOCR-VL API call example or task creation area.
4. Copy the `TOKEN` value, or the token after `Authorization: bearer ...`.
5. When `tree-run setup` asks for `PaddleOCR API key`, paste that token.

Do not put the PaddleOCR API key in README, screenshots, or Git commits. T.R.E.E. writes it to the local `.env`, which is ignored by Git.

When entering API keys, the terminal does not display any characters. This is normal hidden input, like typing a password. Paste or type the full key, then press Enter. After setup, run `tree-run models` to verify that keys are `set` / `not set`; real secrets are never printed.

Model names must exactly match the names supported by your provider. Do not include spaces, terminal color fragments, or extra characters. For example, DeepSeek currently expects `deepseek-v4-pro` or `deepseek-v4-flash`, not `deepseek-v4-pro[1m]`. If a model was entered incorrectly, fix it with:

```bash
tree-run models \
  --base-url https://api.deepseek.com/v1 \
  --examiner deepseek-v4-pro \
  --student deepseek-v4-flash \
  --writer deepseek-v4-flash \
  --archivist deepseek-v4-flash
```

If `tree-run setup` still asks for `PaddleOCR job API URL` or `PaddleOCR model`, your installed checkout is old. From the project root, run:

macOS / Linux:

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

Start the pipeline. The recommended path is interactive mode:

macOS / Linux:

```bash
source .venv/bin/activate
tree-run
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
tree-run
```

At the `TREE>` prompt, type:

```text
/continue
```

You can also skip interactive mode and run `tree-run continue` directly. On every `/continue` or `tree-run continue`, tree checks `raw_materials/`:

- new or changed materials are processed through OCR -> Archivist -> source embedding
- embedding starts as soon as the first source material is produced
- the exam-writing loop starts only after all source materials are embedded
- if no new material exists, the loop resumes from `tree_engine/.runtime/pipeline-state.json`

Resume:

```bash
tree-run continue
```

Manual ingest:

```bash
tree-run ingest --input raw_materials/lectures --collection lectures
tree-run ingest --input raw_materials/lectures --collection lectures --no-structure
tree-run ingest --input raw_materials/lectures --collection lectures --no-index
```

### CLI Commands

Interactive mode:

```bash
tree-run
```

At the `TREE>` prompt, type:

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
| `status` | Show chapter progress |
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

- Source materials are deleted from `tree_engine/.runtime/source_materials/` after indexing.
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
ruff check tree_engine/tree tree_engine/rag tree_engine/ingest
python -m compileall tree_engine/tree tree_engine/rag tree_engine/ingest
```

For end-to-end verification, place real materials in `raw_materials/`, start the embedding server, and run:

```bash
tree-run continue
```

### FAQ

**`Source materials exist but RAG indexer is unavailable`**

Start the embedding server and make sure RAG dependencies are installed:

```bash
pip install ".[rag]"
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell:

```powershell
pip install ".[rag]"
tree_engine\scripts\start-embed-server.bat
```

**`tree-run` cannot import the local package**

If you see `ModuleNotFoundError: No module named 'tree'`, an old editable install probably did not bind the source path correctly. From the project root, reinstall in non-editable mode:

macOS / Linux:

```bash
source .venv/bin/activate
pip install --force-reinstall ".[rag]"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install --force-reinstall ".[rag]"
```

Or use the source-checkout fallback.

macOS / Linux:

```bash
PYTHONPATH=tree_engine python -m tree.cli --help
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "tree_engine"
python -m tree.cli --help
```

**GitHub repository name**

The remote URL is now:

```text
https://github.com/Waylon524/tree.git
```

The GitHub repository name is displayed as `tree`.

### License

MIT. See [LICENSE](LICENSE).
