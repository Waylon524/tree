# TREE

**T.R.E.E.**（Textbook Refinement & Enhancement Engine）是一个资料驱动、以考促写的自动化教材生成引擎。它从课程资料出发，先把 PDF、图片、Word、PPT、Markdown 等材料整理成可验证的知识 DAG，再按 DAG 中的单个 KnowledgeNode 运行 Examiner / Student / Writer 循环，最终把通过盲测的教材 Markdown 写入 `outputs/`。

TREE 的核心目标不是简单总结资料，而是生成能够被“零基础学生”盲测通过的教材内容。LLM 负责语义判断和写作，程序负责契约校验、行号覆盖、DAG 构建、RAG 检索边界、NodeRun 调度和状态持久化。

## 目录

- [当前能力](#当前能力)
- [整体流程](#整体流程)
- [安装](#安装)
- [卸载与删除](#卸载与删除)
- [初始化与配置](#初始化与配置)
- [准备资料](#准备资料)
- [运行 TREE](#运行-tree)
- [查看进度与产物](#查看进度与产物)
- [Embedding 服务](#embedding-服务)
- [高级/开发者命令](#高级开发者命令)
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
- 自动 DAG SVG：生成 `knowledge-dag.json` 后会自动生成 `.tree/runtime/planner/knowledge-dag.svg`，并同步写入 `outputs/knowledge-dag.svg`，节点主体显示 `NNN. 知识点标题`，方便和后续 output 文件对应。
- NodeRun 运行层：取消 branch 切割，Examiner 每次只为 1 个 KnowledgeNode 出题，最多 5 个 active node 并行。
- RAG 边界控制：Student 只读取当前草稿和已完成先修 node 的 finished-output RAG 命中片段，不能直接读取 source 原文或未来/旁支输出。
- 进度面板：交互式 `/watch` 展示 OCR / Clean / Cut / Embed / Cluster / Link / NodeRun 七个阶段的进度条。

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

TREE 要求 Python `>=3.12`，建议优先使用 Python 3.12。下面命令统一写作 `python`；
如果你电脑上的 `python --version` 低于 3.12，请改用你本机可用的 3.12+ 解释器命令，
例如 `python3.12`。普通用户推荐用 `pipx` 安装；安装后可以在任意课程文件夹运行 `tre`
进入交互式 `TREE>` shell。

macOS / Linux：

```bash
python --version
git --version
python -m pip install --user pipx
python -m pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

macOS 用户如果已经使用 Homebrew，也可以用 Homebrew 安装 `pipx`：

```bash
brew install pipx
pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

Windows：

TREE 暂时不支持原生 Windows（PowerShell / CMD）完整运行。完整端到端流程依赖本地
embedding / RAG 组件，其中 `llama-cpp-python` 等原生依赖在 Windows 上安装和运行不稳定。
Windows 用户请优先使用 WSL2 Ubuntu，在 WSL2 里按 Linux 环境安装和运行 TREE。

先在 Windows PowerShell 中安装 WSL。`wsl --install` 会自动安装默认 Ubuntu；如果系统提示重启，
请重启后重新打开 PowerShell，再执行 `wsl` 进入 Ubuntu 终端：

```powershell
wsl --install
wsl
```

然后在 WSL2 Ubuntu 终端中安装 Git、Python、pipx，并用 pipx 安装 TREE：

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv pipx

python3 --version
git --version
pipx --version
pipx ensurepath
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

请确认 `python3 --version` 是 `3.12` 或更高。如果 `pipx ensurepath` 修改了 PATH，请关闭当前
Ubuntu 终端并重新执行 `wsl` 进入。安装后在 WSL2 的 Linux 文件系统中创建课程目录并运行：

```bash
mkdir -p ~/courses/my-class
cd ~/courses/my-class
tre
```

首次启动时，TREE 会检查本机是否已有 `Qwen3-Embedding-0.6B-Q8_0.gguf`。如果没有，TREE 会自动从 Hugging Face 下载模型并启动本地 embedding server；后续运行会复用本机缓存。

在 WSL2 中运行时，建议把课程 workspace、`.tree/runtime/` 和 embedding 模型都放在 WSL2
自己的 Linux 文件系统中，例如 `~/courses/my-class/`，不要直接放在 `/mnt/c/...` 下，以免文件
IO 和本地向量库访问明显变慢。

即可进入 `TREE>` 交互界面。

推荐的交互式流程：

```text
tre
/init
/setup
# 将 PDF / PPTX / DOCX / Markdown / 文本资料放入 materials/
/materials
/run
/watch
/dag
/quit
```

### 更新

如果使用 `pipx` 安装：

```bash
pipx upgrade tree-engine
```

如果需要强制从 GitHub 重新安装：

```bash
pipx uninstall tree-engine
pipx install "tree-engine[rag] @ git+https://github.com/Waylon524/tree.git"
```

更新不会删除课程工作区中的 `materials/`、`outputs/` 和 `.tree/`。

## 卸载与删除

如果 TREE 或 embedding server 正在运行，先在 `TREE>` 中执行 `/quit`，或在终端中执行：

```bash
tre embedding stop
tre stop
```

卸载通过 `pipx` 安装的 TREE 程序：

```bash
pipx uninstall tree-engine
```

这只会删除 `tree-engine` 命令和它的 Python 环境，不会删除课程 workspace 中的资料、输出或运行时文件。

清理当前课程 workspace 的运行时产物：

```bash
tre clean
```

`tre clean` 只删除当前目录下的 `.tree/runtime/`，不会删除 `materials/` 或 `outputs/`。如果你确定要删除当前 workspace 的全部 TREE 配置和运行状态，可以手动删除：

```bash
rm -rf .tree
```

删除自动下载的默认 embedding 模型缓存：

```bash
rm -rf ~/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B-GGUF
```

如果你设置过 `HF_HOME`，Hugging Face 缓存会在 `$HF_HOME/hub/` 下；请删除其中的 `models--Qwen--Qwen3-Embedding-0.6B-GGUF` 目录。

删除 TREE 的全局配置和全局 embedding 服务状态：

```bash
rm -rf ~/.tree
```

这会删除全局 `config.env`、embedding server 的 pid/log 等服务状态。执行前请确认你不再需要其中保存的 API 配置。

## 初始化与配置

在一个课程目录中运行 `tre`，进入 `TREE>` 后执行：

```text
/init
/setup
```

`/init` 会创建：

```text
materials/
outputs/
.tree/
```

`/setup` 会启动交互式配置向导，默认写入全局配置 `~/.tree/config.env`，所有 TREE workspace 都会复用这份配置。向导会依次引导输入：

- Shared LLM / agent API key
- LLM base URL
- Default LLM model
- Examiner / Student / Writer / Archivist / Dagger 五个角色模型
- PaddleOCR API key

### 推荐 LLM 模型

普通用户推荐使用 DeepSeek 的 `deepseek-v4-flash`。先在 [DeepSeek API Keys](https://platform.deepseek.com/api_keys) 页面创建 API key，然后在 `/setup` 中按下面填写：

```text
Shared LLM / agent API key: <你的 DeepSeek API key>
LLM base URL: https://api.deepseek.com
Default LLM model: deepseek-v4-flash
```

五个角色模型如果没有特殊需求，可以直接回车接受默认值，复用 `deepseek-v4-flash`。

### PaddleOCR API Key 获取方法

TREE 使用 PaddleOCR 处理 PDF 和图片资料的版式、公式、表格和图像内容。PaddleOCR 是开源 OCR 项目，当前官方服务每天提供 20000 页免费解析额度。

获取 API Key：

1. 打开 [PaddleOCR 服务页面](https://aistudio.baidu.com/paddleocr)。
2. 登录或注册百度 AI Studio 账号。
3. 在页面中开通 / 创建 PaddleOCR 服务，并复制生成的 API Key。
4. 回到 `TREE>` 运行 `/setup`，在 `PaddleOCR API key` 步骤粘贴该 Key。

TREE 有五个 LLM 角色。普通用户可以在 `/setup` 中直接接受默认角色模型，也可以按需覆盖：

```text
examiner   出题、批改、判断 PASS/FAIL
student    零基础学生，只基于允许的资料答题
writer     根据瓶颈报告写作或修补教材
archivist  清洗 OCR Markdown、切 MTU、局部 repair
dagger     聚类 MTU、命名 node、选择 required_defines、修复 DAG 冲突
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

放好资料后，在 `TREE>` 中执行 `/materials`，确认 TREE 能看到这些文件。

## 运行 TREE

### 交互式运行

在课程工作区运行：

```bash
tre
```

进入 `TREE>` 后常用 slash commands：

```text
/init       初始化当前 TREE workspace
/setup      运行全局配置向导
/materials  列出 materials/ 下支持的资料
/run        后台启动完整 pipeline，并自动准备 embedding
/watch      实时刷新全流程进度面板，按 ESC 退出
/status     查看当前 workspace 状态
/dag        生成或刷新 outputs/knowledge-dag.svg
/stop       只停止后台 engine，保留 embedding server
/quit       停止后台 engine 和 TREE 托管的 embedding server，并离开 shell
/help       查看交互命令
```

`/start` 仍作为 `/run` 的兼容别名可用，但不再作为推荐命令展示。需要打印原始 `progress.json` 时，可以查看下面的“高级/开发者命令”折叠目录。

普通用户只需要保留这个 shell：`/run` 会在后台启动完整 pipeline，`/watch` 会实时显示进度，`/dag` 会把 DAG 图写入 `outputs/knowledge-dag.svg`。如果只想停掉当前后台 engine 但保留 embedding server，使用 `/stop`；如果要完整退出 TREE，使用 `/quit`。

## 查看进度与产物

### `/watch`

`/watch` 是实时刷新的进度面板，按 `ESC` 退出，显示七个阶段与最近错误信息：

```text
TREE Watch
Overview
  materials 6  nodes 74  active 4  exit Press ESC

Progress
  Stage    Progress            %   Count Status   Current
  OCR      ████████████████ 100%     6/6 COMPLETE
  Clean    ████████████████ 100%     6/6 COMPLETE
  Cut      ████████████████ 100%     6/6 COMPLETE
  Embed    ███████░░░░░░░░░  46%   34/74 RUNNING 当前: ...
  Cluster  ░░░░░░░░░░░░░░░░   0%     0/0 WAIT
  Link     ░░░░░░░░░░░░░░░░   0%     0/0 WAIT
  NodeRun  █░░░░░░░░░░░░░░░   5%    4/74 RUNNING 当前: 001. A, 002. B

Errors
- none
```

七个阶段含义：

- `OCR`：原始资料抽取 / OCR 完成数量。
- `Clean`：Archivist clean chunk 完成数量。
- `Cut`：Archivist cut_mtus chunk 完成数量。
- `Embed`：source MTU 写入 Qdrant / node_id 回填进度。
- `Cluster`：Dagger cluster refinement 进度。
- `Link`：Dagger prerequisites 与 deterministic edge construction 进度。
- `NodeRun`：已 PASS 的 KnowledgeNode 数量和 active node。

### DAG 图

执行 `/dag` 后，用户可见的 DAG 图会写入：

```text
outputs/knowledge-dag.svg
```

节点名称与 output 编号对齐，适合用来检查教材生成顺序和知识依赖关系。

### 教材 outputs

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

项目内置 Qwen3 Embedding GGUF server，默认使用更轻量的 0.6B Q8 模型。普通用户不需要手动启动：进入 `TREE>` 后，`/run` 会在需要 RAG 时自动检查模型、下载缺失模型并启动本地 embedding server；`/quit` 会停止 TREE 托管的 engine 和 embedding server。

默认模型：

```text
Qwen/Qwen3-Embedding-0.6B-GGUF
Qwen3-Embedding-0.6B-Q8_0.gguf
```

如果 Hugging Face 官方站访问慢或无法访问，可以让自动下载改用 mirror endpoint：

```bash
EMBED_HF_ENDPOINT=https://hf-mirror.com tre
```

长期使用可以写入 shell 配置：

```bash
export EMBED_HF_ENDPOINT=https://hf-mirror.com
```

`EMBED_HF_ENDPOINT` 只影响 embedding 模型文件下载；模型启动后，TREE 仍然默认访问本机 `http://localhost:8788/v1/embeddings`。

也可以先下载 GGUF 文件并指定本地路径：

```bash
EMBED_MODEL_PATH=/path/to/Qwen3-Embedding-0.6B-Q8_0.gguf tre
```

如果 `EMBED_API_URL` 指向非本机地址，TREE 会认为你使用外部 embedding endpoint，并跳过本地模型下载和 server 自动启动。

## 高级/开发者命令

<details>
<summary>高级/开发者命令</summary>

### 从源码 checkout 运行

macOS / Linux：

```bash
git clone <TREE_REPOSITORY_URL> Tree
cd Tree
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[rag,dev]"
tre doctor
```

Windows：

暂不支持在原生 Windows PowerShell / CMD 中运行源码开发环境。Windows 开发请使用 WSL2
Ubuntu，并在 WSL2 终端中执行与 macOS / Linux 相同的步骤：

```bash
git clone <TREE_REPOSITORY_URL> Tree
cd Tree
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[rag,dev]"
tre doctor
```

只跑不依赖 RAG 的单元开发时，可以安装：

```bash
pip install -e ".[dev]"
```

### 外层 CLI 命令

这些命令保留给调试、自动化、CI 或需要绕过交互式 shell 的场景：

```bash
tre doctor               # 只读体检
tre setup                # 交互式全局配置向导
tre setup --workspace    # 交互式当前工作区覆盖配置
tre models               # 查看五个角色当前模型
tre prompts              # 列出内置 prompt 角色名
tre clean                # 删除 .tree/runtime/，不删除 materials/ 和 outputs/

tre run                  # 前台运行完整 pipeline
tre start                # 后台启动 engine
tre stop                 # 停止后台 engine
tre quit                 # 停止后台 engine 和 TREE 托管的 embedding server
tre resume               # 等同于 tre run
tre continue             # 等同于 tre run

tre status               # 简短状态：phase/message/materials/nodes/edges/active nodes
tre progress             # 打印完整 progress.json
tre watch                # 实时显示七阶段进度条和错误信息，按 ESC 退出
tre materials            # 列出支持的资料文件
tre logs                 # 列出 runtime log 文件

tre ingest --input /path/to/file.pdf --collection 课件
tre ingest --input /path/to/folder --collection 课件
tre planner rebuild
tre planner dag-svg

tre rag status
tre rag inventory
tre rag nodes
tre rag graph
tre rag search "化学平衡常数" --top-k 5

tre embedding install
tre embedding status
tre embedding start
tre embedding stop
```

### 脚本式配置

脚本或 CI 中可以直接传入参数，非交互写入目标配置。默认写全局配置；加 `--workspace` 写当前工作区：

```bash
tre setup \
  --llm-api-key "$LLM_API_KEY" \
  --llm-base-url "https://api.deepseek.com" \
  --llm-model "deepseek-v4-flash" \
  --paddleocr-api-token "$PADDLEOCR_API_TOKEN" \
  --paddleocr-api-url "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
```

配置加载顺序为：

```text
~/.tree/config.env -> ./.env -> ./.tree/config.env
```

后加载的文件会覆盖先加载的文件；空值不会覆盖已有值。

最小配置模板：

```bash
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash

PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_API_TOKEN=
PADDLEOCR_MODEL=PaddleOCR-VL-1.6
```

角色模型和运行参数也可以通过环境变量覆盖：

```bash
EXAMINER_MODEL=
STUDENT_MODEL=
WRITER_MODEL=
ARCHIVIST_MODEL=
DAGGER_MODEL=

MAX_ITERATIONS=5
SOURCE_INGEST_CONCURRENCY=16
SOURCE_OCR_CONCURRENCY=5
DAGGER_EMBED_CLUSTER_ENABLED=true
DAGGER_PREREQUISITE_CONCURRENCY=5
MAX_ACTIVE_NODE_RUNS=5
```

### Embedding 手动控制

高级用法仍可直接运行 server：

```bash
python -m tree.rag.server
python -m tree.rag.server --n-gpu-layers 0
python -m tree.rag.server --host 127.0.0.1 --port 8788
```

Embedding 相关环境变量：

```bash
EMBED_API_URL=http://localhost:8788
EMBED_MODEL=Qwen3-Embedding-0.6B-Q8_0
EMBED_MODEL_PATH=
EMBED_HF_ENDPOINT=
EMBED_AUTO_DOWNLOAD=true
EMBED_AUTO_START=true
EMBED_SERVER_START_TIMEOUT_SEC=300
```

健康检查：

```bash
curl http://localhost:8788/health
```

测试 embedding：

```bash
curl -X POST http://localhost:8788/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-Embedding-0.6B-Q8_0","input":"化学平衡状态是正逆反应速率相等的状态"}'
```

如果你希望在 macOS 上使用 Metal，需要确保 `llama-cpp-python` 是带 Metal 支持编译安装的版本。

### Planner artifacts

Planner 内部产物位于：

```text
.tree/runtime/planner/material-manifest.json
.tree/runtime/planner/mtus.json
.tree/runtime/planner/knowledge-nodes.json
.tree/runtime/planner/knowledge-dag.json
.tree/runtime/planner/knowledge-dag.svg
outputs/knowledge-dag.svg
```

</details>

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

如果刚运行过 `pipx ensurepath`，请重新打开安装 TREE 的终端，然后检查。macOS / Linux /
WSL2 Ubuntu：

```bash
pipx list
```

如果需要从 Windows PowerShell 检查 WSL2 内的 `pipx` 安装状态：

```powershell
wsl -e bash -lc "pipx list"
```

### 缺少 LLM 配置

如果看到类似：

```text
No LLM_API_KEY or role-specific API key found
```

请进入 `TREE>` 后运行 `/setup`，重新写入 LLM 和 PaddleOCR 配置。

至少需要设置 `LLM_API_KEY`，或设置角色级 `EXAMINER_API_KEY` / `STUDENT_API_KEY` / `WRITER_API_KEY` / `ARCHIVIST_API_KEY` / `DAGGER_API_KEY`。

### PaddleOCR 未配置

请确认：

```bash
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_API_TOKEN=...
```

如果 OCR API 可访问但资料为空或格式不支持，进入 `TREE>` 后用 `/materials` 确认当前 `materials/` 中有哪些文件会被 TREE 处理。

### RAG indexer unavailable

完整端到端运行需要安装 `[rag]`。如果本地没有 embedding 模型，首次启动 TREE 会自动下载并启动本地 embedding server。

macOS / Linux / WSL2：

```bash
pip install -e ".[rag,dev]"
```

回到同一 workspace 后进入 `TREE>`，运行 `/run`。

原生 Windows 暂不支持本地 `[rag]` 安装和运行。请改用 WSL2 Ubuntu，或在后续版本支持外部
embedding endpoint 后配置 `EMBED_API_URL`。

### 清理运行时产物

运行流程不会删除 `materials/` 或 `outputs/`。如果开发者需要清理 `.tree/runtime/` 后重新验收，请查看上面的“高级/开发者命令”折叠目录。

## License

MIT
