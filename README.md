# tree

**Exam-driven textbook generation from raw course materials.**

[中文](#中文) | [English](#english)

---

## 中文

tree（Textbook Refinement & Enhancement Engine）是一套资料驱动的自动化教材生成流水线。用户把课件、习题、讲义、图片或文本资料放入 `materials/` 后，引擎会自动完成 OCR、结构化整理、本地向量化入库，并通过“以考促写”的循环持续生成教材内容。

### 工作流程

```text
materials/
  -> PaddleOCR-VL-1.6
  -> Archivist 结构化清洗
  -> source RAG
  -> Source Inventory
  -> KnowledgeGroups
  -> KnowledgeNodes
  -> KnowledgeDAG / KnowledgeBranches
  -> BranchRun 调度
  -> Examiner 在 active branch span 内命题
  -> Student 盲测
  -> Examiner 批改
  -> Writer 写 declared branch span 教材
  -> outputs/
```

### 安装

安装前需要准备：

- Python `>=3.12`
- Git
- 一个 OpenAI-compatible Chat Completions API key
- PaddleOCR API token
- 能访问 Hugging Face 或已配置代理，因为首次启动 embedding server 会下载约 4.3 GB 的本地模型

如果本机还没有 Python 或 Git，先安装 [Python](https://www.python.org/downloads/) 和 [Git](https://git-scm.com/downloads/)。安装后重新打开终端，再运行版本检查命令。

安装完成后，在任意课程文件夹中输入 `tre` 即可启动交互界面；当前文件夹会成为一个独立 workspace。

#### macOS

推荐使用 Homebrew 安装 `pipx`：

```bash
python3.12 --version
git --version
brew install pipx
pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

如果 `brew` 命令不存在，可以先安装 [Homebrew](https://brew.sh/)，或者使用下面的无 Homebrew 方式：

```bash
python3.12 --version
git --version
python3.12 -m venv ~/.local/pipx-venv
~/.local/pipx-venv/bin/python -m pip install -U pip pipx
~/.local/pipx-venv/bin/python -m pipx ensurepath
~/.local/bin/pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

如果 `pipx ensurepath` 修改了 PATH，请重新打开终端。然后进入已有课程文件夹并启动：

```bash
cd /path/to/your/course-folder
tre
```

进入 `TREE>` 后使用 `/start`、`/watch`、`/status`、`/stop`、`/quit` 等 slash commands。

#### Linux

```bash
python3.12 --version
git --version
python3.12 -m pip install --user pipx
python3.12 -m pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

如果 `pipx ensurepath` 修改了 PATH，请重新打开终端。然后进入已有课程文件夹并启动：

```bash
cd /path/to/your/course-folder
tre
```

进入 `TREE>` 后使用 `/start`、`/watch`、`/status`、`/stop`、`/quit` 等 slash commands。

#### Windows PowerShell

```powershell
py -3.12 --version
git --version
py -3.12 -m pip install --user pipx
py -3.12 -m pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

如果 `pipx ensurepath` 修改了 PATH，请重新打开 PowerShell。然后进入已有课程文件夹并启动：

```powershell
cd C:\path\to\your\course-folder
tre
```

进入 `TREE>` 后使用 `/start`、`/watch`、`/status`、`/stop`、`/quit` 等 slash commands。

第一次在某个文件夹运行 `tre` 时，CLI 会自动创建：

```text
materials/
outputs/
.tree/
```

`materials/` 放用户资料，`outputs/` 放最终教材，`.tree/` 保存当前 workspace 的状态、RAG、草稿和日志。全局 API 配置与 embedding 服务状态保存在用户目录 `~/.tree/`。

安装后可以随时运行体检：

```bash
tre doctor
```

`doctor` 会检查 Python、`tre` 是否在 PATH 中、包安装位置、`TREE_HOME`、全局配置、当前 workspace 目录、embedding server 和 Git 状态。它不会修改配置。

`/progress` 和 `/watch` 会读取 `.tree/runtime/progress.json` 与 `.tree/runtime/knowledge-graph.json`，显示 OCR 页级进度、source embedding 进度、当前知识点阶段，以及 Current Tree 面板。Current Tree 会展示已生成 node 之间的 parent / support 关系和正在生成的 node；如果旧数据或中断状态导致 parent 信息不完整，则自动降级为关系表。

### 更新

如果通过 `pipx install` 安装，更新前建议先在任意 workspace 停止后台服务：

```bash
tre quit
```

然后更新引擎：

```bash
pipx upgrade tree-engine
```

如果想强制从 GitHub 重新安装最新版：

```bash
pipx uninstall tree-engine
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

更新后检查：

```bash
tre --help
tre doctor
```

更新不会删除课程文件夹中的 `materials/`、`outputs/`、`.tree/`，也不会删除用户目录 `~/.tree/config.env`。API key、模型配置和已有 workspace 状态会保留。

### 配置

第一次运行需要配置的命令时，例如 `tre start` 或 `tre ingest`，如果用户目录没有全局配置，CLI 会自动启动交互式配置向导。也可以手动运行：

```bash
tre setup
```

配置向导会要求输入：

- PaddleOCR API key
- 子智能体共享 API key
- LLM base URL
- 默认模型
- `Examiner`、`Student`、`Writer`、`Archivist` 四个角色的模型

PaddleOCR job URL 和 PaddleOCR model 是固定值，不需要填写：

```text
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_MODEL=PaddleOCR-VL-1.6
```

获取 PaddleOCR API key：

1. 打开 [PaddleOCR API 任务页](https://aistudio.baidu.com/paddleocr/task)。
2. 登录百度 AI Studio / 飞桨账号。
3. 在页面中找到 PaddleOCR-VL 的 API 调用示例或任务创建区域。
4. 复制示例里的 `TOKEN` 或 `Authorization: bearer ...` 后面的 token。
5. 运行 `tre setup` 时，把 token 粘贴到 `PaddleOCR API key` 输入项。

### 使用教程

把资料放入 `materials/`。子目录名会作为 source collection：

```text
materials/
├── 课件/
│   ├── 5. 化学平衡通论.pdf
│   └── 6. 化学动力学简介.pptx
└── 作业/
    ├── 普通化学A-作业2026-01.pdf
    └── 普通化学A-作业2026-02.pdf
```

支持 PDF、PPT/PPTX、图片、DOCX、Markdown、TXT 等格式，具体以后缀集合 `tree.engine.RAW_MATERIAL_EXTENSIONS` 为准。超过 100 页的 PDF 会在上传 PaddleOCR 前自动切分成每组不超过 100 页的临时 PDF，OCR 返回后再按顺序拼接成完整 Markdown 交给 Archivist。PPTX 会通过 Python 提取文本、表格、备注和内嵌图片 OCR；旧版 PPT 使用纯文本兜底提取。为获得更好的版式、公式、图表和图片识别效果，建议用户手动将 PPT/PPTX 转成 PDF 后再放入 `materials/`。

#### macOS / Linux

```bash
tre
```

#### Windows PowerShell

```powershell
tre
```

进入 `TREE>` 后常用：

```text
/start      # 后台启动 TREE，自动确保 embedding server 运行
/watch      # 持续刷新当前进度，按 Esc 或 Ctrl+C 回到 TREE>
/progress   # 显示一屏进度快照
/status     # 查看服务和章节状态
/stop       # 停止 TREE，保留 embedding server
/quit       # 停止 TREE 和 embedding server
/help       # 查看交互命令
```

日常使用只需要留在 `TREE>` 里输入这些 slash commands。每次 `/start` 都会先检查 `materials/`：

- 如果 `materials/` 中没有任何受支持的资料文件，启动会报错并提示先放入资料。
- 有新增或变更资料：先执行 OCR -> Archivist -> source embedding。
- 第一个 source material 生成后即可开始串行 embedding。
- 所有 source materials embedding 完成后，先构建 KnowledgeGroups、KnowledgeNodes、KnowledgeDAG 和 KnowledgeBranches，再进入 BranchRun 考试-写作循环。
- 有资料但没有新增或变更：直接从 `.tree/runtime/pipeline-state.json` 恢复循环。

强行关闭 `TREE` 交互界面（例如 Ctrl+C、终端关闭或输入流断开）会自动执行 `/quit`，停止 TREE 和 embedding server。只有主动输入 `/exit` 才会只离开交互界面而保留后台服务。

更多命令、手动摄入和排错用法见下方高级设置。

<details>
<summary>高级运行机制、RAG、PaddleOCR、embedding server 和项目结构</summary>

#### 高级命令

手动摄入某个文件或目录：

```bash
tre ingest --input materials/课件 --collection 课件
tre ingest --input materials/课件 --collection 课件 --no-structure
tre ingest --input materials/课件 --collection 课件 --no-index
```

常用一次性命令：

```bash
tre --help
tre start
tre status
tre progress
tre watch
tre stop
tre quit
tre doctor
tre materials
tre logs --tail 20
tre models
tre rag status
tre rag inventory
tre rag candidates
tre rag graph
tre rag search "化学平衡常数" --kind source --top-k 5
```

其他命令可运行 `tre --help` 或在 `TREE>` 中输入 `/help` 查看。

#### 高级配置

重新运行全局配置向导：

```bash
tre setup --force
```

只为当前 workspace 写入覆盖配置：

```bash
tre setup --workspace
```

全局配置保存在 `~/.tree/config.env`；workspace 覆盖配置保存在 `.tree/config.env`。

不要把 API key 写进 README、截图或提交到 Git。输入 API key 时终端不会显示任何字符，这是正常的隐藏输入，类似输入密码。

`LLM base URL` 和模型名请按你的供应商文档填写。模型名必须完全匹配供应商支持的名称，不要带空格、颜色控制残留或多余字符。

后续修改模型和供应商配置：

```bash
tre models
tre models --help
tre models --api-key
tre models --paddleocr-key
```

生成的配置文件大致如下。PaddleOCR URL 和 model 由 CLI 自动写入固定值：

```bash
# OpenAI-compatible LLM
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=

# Optional role-specific overrides
EXAMINER_MODEL=
STUDENT_MODEL=
WRITER_MODEL=
ARCHIVIST_MODEL=

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

任何兼容 OpenAI Chat Completions API 的供应商或自托管网关都可以使用。每个角色也支持独立配置 `EXAMINER_API_KEY`、`EXAMINER_BASE_URL`、`EXAMINER_MODEL`，`STUDENT_*`、`WRITER_*`、`ARCHIVIST_*` 同理。

#### 从源码 checkout 运行

macOS / Linux：

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[rag]"
tre
```

Windows PowerShell：

```powershell
git clone https://github.com/Waylon524/tree.git engine
cd engine
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[rag]"
tre
```

#### Agent 工作流

| Role | Prompt | 作用 |
| --- | --- | --- |
| Examiner | `EXAMINER_PROMPT` | 按 Knowledge Graph Planner 选中的 node 命题、批改、判断 PASS/FAIL |
| Student | `STUDENT_PROMPT` | 零基础学生，只基于当前草稿、已学全文和 learned RAG hits 作答 |
| Writer | `WRITER_PROMPT` | 根据抽象 Bottleneck Report 和 graph node delta 创建或优化教材草稿 |
| Archivist | `ARCHIVIST_PROMPT` | 对 PaddleOCR 输出做轻量清洗和 Markdown 标准化 |

#### RAG 策略

- source materials 写入 RAG 后删除 `.tree/runtime/source_materials/` 中的中间 Markdown。
- finished outputs 保留在 `outputs/`，同时写入 RAG。
- drafts 不写入 RAG，Student 直接读取当前 draft 全文。
- Source RAG 先被整理成文件内顺序 KnowledgeGroups，再跨文件合并为 canonical KnowledgeNodes。
- Planner 根据 KnowledgeNodes、finished ledger、依赖边、相邻关系和 source overlap 构建 KnowledgeDAG 与 KnowledgeBranches，并调度 ready BranchRuns。
- Examiner 命题只在 ActiveBranch Context 内声明连续 `Covered_Node_IDs`，不能选择 root、branch 或全局方向。
- Student 答题会使用已学习 finished outputs 的 RAG 检索，并直接阅读当前 draft；Learned RAG Hit 视为已学成品教材摘录，不是 source material。
- Examiner 审核时可用 source RAG 判断 writer 应补什么，但 source RAG 不能作为 student faithfulness 的证据。
- Writer 会接收 branch span context，只写 declared branch span；上游 ancestor nodes 和当前 branch 前序文件只能作为先修引用，不应重讲。
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

#### PaddleOCR-VL-1.6

默认模型：

```text
PADDLEOCR_MODEL=PaddleOCR-VL-1.6
```

OCR job 使用：

```python
optionalPayload = {
    "useDocOrientationClassify": True,
    "useDocUnwarping": True,
    "useChartRecognition": True,
}
```

OCR 上传默认每 5 秒提交一个文件；上传和轮询可以并发；Archivist 可以多并发处理；embedding 默认串行。

#### 本地 Embedding 模型

tree 默认使用 `Qwen/Qwen3-Embedding-4B-GGUF` 中的 `Qwen3-Embedding-4B-Q8_0.gguf`。首次启动 embedding server 时会自动下载模型，文件大小约 4.3 GB。下载完成后模型会留在本机 Hugging Face 缓存中，之后换工作区通常不需要重新下载。

`pip install ".[rag]"` 已经安装 embedding server 所需的 Python 依赖。`setup-embedding.sh` 主要用于 macOS / Linux 上重新编译或强制选择 Metal/CUDA/CPU 版本的 `llama-cpp-python`。

macOS / Linux：

```bash
./tree_engine/scripts/setup-embedding.sh
./tree_engine/scripts/setup-embedding.sh --device metal
./tree_engine/scripts/setup-embedding.sh --device cpu
./tree_engine/scripts/setup-embedding.sh --device cuda
```

Windows PowerShell 用户通常不需要运行 `setup-embedding.sh`；它是 macOS / Linux shell 脚本。

`tre start` 和 `/start` 会后台管理 embedding server。手动前台启动仅建议用于源码调试：

macOS / Linux：

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell：

```powershell
tree_engine\scripts\start-embed-server.bat
```

前台启动会占用当前终端。运行 `tre setup`、`tre start`、`tre ingest` 等命令时，请新开终端标签页，回到同一个 workspace。

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

#### 项目结构

```text
my-course/
├── materials/             # 用户上传原始资料
├── outputs/               # 通过考试的最终教材
└── .tree/                  # 当前 workspace 的内部状态
    ├── config.env          # 可选：仅覆盖当前 workspace 的配置
    └── runtime/
        ├── source_materials/
        ├── drafts/
        ├── knowledge-ledger.json
        ├── source-inventory.json
        ├── candidate-nodes.json
        ├── knowledge-graph.json
        ├── pipeline-temp/
        ├── rag-store/
        └── services/
```

全局目录：

```text
~/.tree/
├── config.env           # 默认 API 与模型配置
└── services/            # 全局 embedding server 的 pid/log
```

</details>

<details>
<summary>故障排查和开发验证</summary>

#### 故障排查

**`Source materials exist but RAG indexer is unavailable`**

说明 embedding server 未启动或 RAG 依赖未安装。

```bash
tre doctor
tre start
```

如果安装时没有带 `[rag]`，请重新安装：

```bash
pipx uninstall tree-engine
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

源码 checkout 的手动前台调试：

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell：

```powershell
tree_engine\scripts\start-embed-server.bat
```

**`tre` 找不到命令**

如果刚刚运行过 `pipx ensurepath`，请重新打开终端。然后检查：

```bash
pipx list
which tre
```

Windows PowerShell：

```powershell
pipx list
Get-Command tre
```

如果仍找不到，通常是 pipx 的 bin 目录没有进入 PATH。重新执行：

macOS：

```bash
pipx ensurepath
```

Linux：

```bash
python3.12 -m pipx ensurepath
```

Windows PowerShell：

```powershell
py -3.12 -m pipx ensurepath
```

**`tre` 无法导入本地包**

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

#### 开发验证

当前仓库不保留内置样例数据。仓库保留必要的回归测试；本地开发时也可以放置更多 ignored 测试文件。修改代码后建议至少执行：

```bash
python -m pytest
ruff check tree_engine tests
python -m compileall tree_engine/tree tree_engine/rag tree_engine/ingest
```

需要端到端验证时，将真实资料放入 `materials/`，然后运行：

```bash
tre start
tre watch
```

</details>

### License

MIT. See [LICENSE](LICENSE).

---

## English

tree (Textbook Refinement & Enhancement Engine) is a material-driven pipeline for generating textbook chapters through exam-driven writing. After users place lecture slides, exercises, handouts, images, or text files in `materials/`, the engine performs OCR, lightweight structuring, local embedding, and an iterative teaching loop.

### Workflow

```text
materials/
  -> PaddleOCR-VL-1.6
  -> Archivist cleanup
  -> source RAG
  -> Source Inventory
  -> KnowledgeGroups
  -> KnowledgeNodes
  -> KnowledgeDAG / KnowledgeBranches
  -> BranchRun scheduler
  -> Examiner composes inside the active branch span
  -> Student blind test
  -> Examiner audit
  -> Writer creates the declared branch span draft
  -> outputs/
```

### Installation

Prepare:

- Python `>=3.12`
- Git
- An OpenAI-compatible Chat Completions API key
- A PaddleOCR API token
- Access to Hugging Face, or a configured proxy, because the first embedding server start downloads about 4.3 GB

If Python or Git is missing, install [Python](https://www.python.org/downloads/) and [Git](https://git-scm.com/downloads/) first. Reopen your terminal after installation, then run the version checks.

After installation, run `tre` from any course folder to open the interactive interface. The current folder becomes an independent workspace.

#### macOS

Recommended: install `pipx` with Homebrew:

```bash
python3.12 --version
git --version
brew install pipx
pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

If `brew` is not available, install [Homebrew](https://brew.sh/) first, or use this no-Homebrew path:

```bash
python3.12 --version
git --version
python3.12 -m venv ~/.local/pipx-venv
~/.local/pipx-venv/bin/python -m pip install -U pip pipx
~/.local/pipx-venv/bin/python -m pipx ensurepath
~/.local/bin/pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

If `pipx ensurepath` changed PATH, reopen the terminal. Then enter an existing course folder and start TREE:

```bash
cd /path/to/your/course-folder
tre
```

Inside `TREE>`, use slash commands such as `/start`, `/watch`, `/status`, `/stop`, and `/quit`.

#### Linux

```bash
python3.12 --version
git --version
python3.12 -m pip install --user pipx
python3.12 -m pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

If `pipx ensurepath` changed PATH, reopen the terminal. Then enter an existing course folder and start TREE:

```bash
cd /path/to/your/course-folder
tre
```

Inside `TREE>`, use slash commands such as `/start`, `/watch`, `/status`, `/stop`, and `/quit`.

#### Windows PowerShell

```powershell
py -3.12 --version
git --version
py -3.12 -m pip install --user pipx
py -3.12 -m pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

If `pipx ensurepath` changed PATH, reopen PowerShell. Then enter an existing course folder and start TREE:

```powershell
cd C:\path\to\your\course-folder
tre
```

Inside `TREE>`, use slash commands such as `/start`, `/watch`, `/status`, `/stop`, and `/quit`.

The first `tre` run in a folder creates:

```text
materials/
outputs/
.tree/
```

Use `materials/` for uploads, `outputs/` for final outputs, and `.tree/` for workspace state, RAG, drafts, and logs. Global API config and embedding service state live under the user-level `~/.tree/` directory.

Run a health check after installation:

```bash
tre doctor
```

`doctor` checks Python, whether `tre` is on PATH, package location, `TREE_HOME`, global config, the current workspace folders, the embedding server, and Git state. It does not modify configuration.

`/progress` and `/watch` read `.tree/runtime/progress.json` and `.tree/runtime/knowledge-graph.json`. They show OCR page progress, source embedding progress, the current knowledge-point stage, and a Current Tree panel. Current Tree shows parent/support relationships among generated nodes plus the node currently being generated. If older data or an interrupted run has incomplete parent metadata, it falls back to a relation table.

### Update

If tree was installed with `pipx install`, first stop background services from any workspace:

```bash
tre quit
```

Then update the engine:

```bash
pipx upgrade tree-engine
```

To force reinstall the latest version from GitHub:

```bash
pipx uninstall tree-engine
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

After updating, check:

```bash
tre --help
tre doctor
```

Updating does not delete `materials/`, `outputs/`, or `.tree/` in course folders, and it does not delete the user-level `~/.tree/config.env`. API keys, model settings, and existing workspace state are preserved.

### Configuration

The first time you run a configuration-dependent command, such as `tre start` or `tre ingest`, the CLI starts an interactive setup wizard if no global config exists. You can also run it manually:

```bash
tre setup
```

The wizard asks for:

- PaddleOCR API key
- shared API key for the agent provider
- LLM base URL
- default model
- role models for `Examiner`, `Student`, `Writer`, and `Archivist`

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
5. When `tre setup` asks for `PaddleOCR API key`, paste that token.

### Usage

Place source files in `materials/`. Subdirectories become source collections:

```text
materials/
├── lectures/
│   ├── 05-equilibrium.pdf
│   └── 06-kinetics.pptx
└── exercises/
    ├── homework-01.pdf
    └── homework-02.pdf
```

Supported inputs include PDF, PPT/PPTX, images, DOCX, Markdown, and TXT. The exact suffix set is defined by `tree.engine.RAW_MATERIAL_EXTENSIONS`. PDFs over 100 pages are automatically split into temporary PDFs of at most 100 pages before PaddleOCR upload; OCR Markdown is then stitched back together in order before Archivist processing. PPTX files are processed with Python by extracting text, tables, notes, and OCR for embedded images; legacy PPT uses a best-effort text fallback. For better layout, formula, chart, and image recognition, users should manually export PPT/PPTX to PDF before placing files in `materials/`.

#### macOS / Linux

```bash
tre
```

#### Windows PowerShell

```powershell
tre
```

At the `TREE>` prompt:

```text
/start      # start TREE in the background and ensure embedding is running
/watch      # refresh current progress until Esc or Ctrl+C returns to TREE>
/progress   # show one progress snapshot
/status     # show service and BranchRun status
/stop       # stop TREE while keeping embedding running
/quit       # stop TREE and embedding
/help       # show interactive commands
```

For daily use, stay inside `TREE>` and type these slash commands. Every `/start` checks `materials/` first:

- if `materials/` contains no supported source files, startup fails and asks you to add materials first
- new or changed materials are processed through OCR -> Archivist -> source embedding
- embedding starts as soon as the first source material is produced
- after all source materials are embedded, tree builds KnowledgeGroups, KnowledgeNodes, the KnowledgeDAG, and KnowledgeBranches before starting BranchRun exam-writing loops
- if materials exist but nothing is new or changed, the loop resumes from `.tree/runtime/pipeline-state.json`

Force-closing the `TREE` interactive shell, such as Ctrl+C, terminal close, or input-stream disconnect, automatically runs `/quit` and stops TREE plus the embedding server. Only typing `/exit` leaves the shell while keeping background services unchanged.

More commands, manual ingest, and troubleshooting usage are in the advanced section below.

<details>
<summary>Advanced runtime design, RAG, PaddleOCR, embedding server, and repository layout</summary>

#### Advanced Commands

Manually ingest a file or directory:

```bash
tre ingest --input materials/lectures --collection lectures
tre ingest --input materials/lectures --collection lectures --no-structure
tre ingest --input materials/lectures --collection lectures --no-index
```

Common one-shot commands:

```bash
tre --help
tre start
tre status
tre progress
tre watch
tre stop
tre quit
tre doctor
tre materials
tre logs --tail 20
tre models
tre rag status
tre rag inventory
tre rag candidates
tre rag graph
tre rag search "equilibrium constant" --kind source --top-k 5
```

Run `tre --help`, or type `/help` inside `TREE>`, for the full command list.

#### Advanced Configuration

Rerun the global setup wizard:

```bash
tre setup --force
```

Write overrides only for the current workspace:

```bash
tre setup --workspace
```

Global config is stored at `~/.tree/config.env`; workspace overrides are stored at `.tree/config.env`.

Do not put API keys in README, screenshots, or Git commits. When entering API keys, the terminal does not display any characters. This is normal hidden input, like typing a password.

Use the `LLM base URL` and model names from your provider documentation. Model names must exactly match the names supported by your provider. Do not include spaces, terminal color fragments, or extra characters.

Update model/provider settings later with:

```bash
tre models
tre models --help
tre models --api-key
tre models --paddleocr-key
```

The generated config file looks roughly like this. The PaddleOCR URL and model are written by the CLI as fixed values:

```bash
# OpenAI-compatible LLM
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=

# Optional role-specific overrides
EXAMINER_MODEL=
STUDENT_MODEL=
WRITER_MODEL=
ARCHIVIST_MODEL=

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

Any OpenAI-compatible Chat Completions provider can be used. Role-specific keys, base URLs, and models are also supported through `EXAMINER_*`, `STUDENT_*`, `WRITER_*`, and `ARCHIVIST_*`.

#### Running From A Source Checkout

macOS / Linux:

```bash
git clone https://github.com/Waylon524/tree.git engine
cd engine
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[rag]"
tre
```

Windows PowerShell:

```powershell
git clone https://github.com/Waylon524/tree.git engine
cd engine
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[rag]"
tre
```

#### Agent Workflow

| Role | Prompt | Purpose |
| --- | --- | --- |
| Examiner | `EXAMINER_PROMPT` | Composes inside the active branch span, audits answers, and decides PASS/FAIL |
| Student | `STUDENT_PROMPT` | Zero-baseline learner using the current draft, prior full outputs, and learned RAG hits |
| Writer | `WRITER_PROMPT` | Creates or optimizes drafts from abstract bottleneck reports and the declared branch span |
| Archivist | `ARCHIVIST_PROMPT` | Cleans PaddleOCR output into normalized Markdown |

#### RAG Strategy

- Source materials are deleted from `.tree/runtime/source_materials/` after indexing.
- Finished outputs remain in `outputs/` and are indexed.
- Drafts are not indexed; the Student reads the current draft directly.
- Source RAG is first converted into file-local KnowledgeGroups, then clustered across files into canonical KnowledgeNodes.
- The planner builds a KnowledgeDAG and KnowledgeBranches from KnowledgeNodes, finished ledger coverage, dependency edges, continuity, and source overlap.
- Examiner exam assembly uses ActiveBranch Context, source RAG, finished-output RAG, and ledger duplicate checks. It must declare continuous `Covered_Node_IDs` and must not choose root, branch, or global direction.
- Student answers use RAG retrieval over already learned finished outputs and direct reading of the current draft. Learned RAG Hits are treated as excerpts from passed outputs, not source material.
- During audit, source RAG may help identify what the Writer should add, but it can never support student faithfulness.
- Writer receives branch-span context and writes only the declared branch span. Ancestor nodes and prior branch files are prerequisites to cite, not material to reteach.
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

#### PaddleOCR-VL-1.6

Default model:

```text
PADDLEOCR_MODEL=PaddleOCR-VL-1.6
```

OCR jobs use:

```python
optionalPayload = {
    "useDocOrientationClassify": True,
    "useDocUnwarping": True,
    "useChartRecognition": True,
}
```

OCR uploads submit one file every 5 seconds by default; upload and polling can run concurrently; Archivist can process multiple files concurrently; embedding is serial by default.

#### Local Embedding Model

tree uses `Qwen3-Embedding-4B-Q8_0.gguf` from `Qwen/Qwen3-Embedding-4B-GGUF` by default. The model is downloaded automatically on first embedding server start. The file is about 4.3 GB and stays in the local Hugging Face cache for later workspaces.

`pip install ".[rag]"` already installs the Python dependencies required by the embedding server. `setup-embedding.sh` is mainly for rebuilding or forcing a Metal/CUDA/CPU `llama-cpp-python` variant on macOS / Linux.

macOS / Linux:

```bash
./tree_engine/scripts/setup-embedding.sh
./tree_engine/scripts/setup-embedding.sh --device metal
./tree_engine/scripts/setup-embedding.sh --device cpu
./tree_engine/scripts/setup-embedding.sh --device cuda
```

Windows PowerShell users usually do not need to run `setup-embedding.sh`; it is a macOS / Linux shell script.

`tre start` and `/start` manage the embedding server in the background. Manual foreground startup is mainly for source-checkout debugging:

macOS / Linux:

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell:

```powershell
tree_engine\scripts\start-embed-server.bat
```

Foreground startup occupies the current terminal. To run `tre setup`, `tre start`, or `tre ingest`, open another terminal tab and return to the same workspace.

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

#### Repository Layout

```text
my-course/
├── materials/             # User uploads
├── outputs/               # Final textbooks
└── .tree/                  # Internal state for this workspace
    ├── config.env          # Optional workspace-only overrides
    └── runtime/
        ├── source_materials/
        ├── drafts/
        ├── knowledge-ledger.json
        ├── source-inventory.json
        ├── candidate-nodes.json
        ├── knowledge-graph.json
        ├── pipeline-temp/
        ├── rag-store/
        └── services/
```

User-level directory:

```text
~/.tree/
├── config.env           # Default API and model config
└── services/            # Global embedding server pid/log
```

</details>

<details>
<summary>Troubleshooting and development verification</summary>

#### Troubleshooting

**`Source materials exist but RAG indexer is unavailable`**

The embedding server is not running or RAG dependencies are missing.

```bash
tre doctor
tre start
```

If tree was installed without `[rag]`, reinstall it:

```bash
pipx uninstall tree-engine
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

Manual foreground debugging from a source checkout:

```bash
./tree_engine/scripts/start-embed-server.sh
```

Windows PowerShell:

```powershell
tree_engine\scripts\start-embed-server.bat
```

**`tre` is not found**

If you just ran `pipx ensurepath`, reopen the terminal. Then check:

```bash
pipx list
which tre
```

Windows PowerShell:

```powershell
pipx list
Get-Command tre
```

If `tre` is still missing, the pipx bin directory is probably not on PATH. Run:

macOS:

```bash
pipx ensurepath
```

Linux:

```bash
python3.12 -m pipx ensurepath
```

Windows PowerShell:

```powershell
py -3.12 -m pipx ensurepath
```

**`tre` cannot import the local package**

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

#### Development Verification

This repository no longer ships built-in sample data. It keeps essential regression tests; local development may also include ignored scratch tests. For code changes, run at least:

```bash
python -m pytest
ruff check tree_engine tests
python -m compileall tree_engine/tree tree_engine/rag tree_engine/ingest
```

For end-to-end verification, place real materials in `materials/`, then run:

```bash
tre start
tre watch
```

</details>

### License

MIT. See [LICENSE](LICENSE).
