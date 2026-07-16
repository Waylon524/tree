# TREE

TREE 是一个桌面学习工作台。它把 PDF、课件、Word、图片、Markdown 等课程资料整理成一棵“知识果树”：采集和清洗资料、建立知识图谱、为知识点生成学习文件，再让你按依赖关系学习、记录阅读进度、对讲解提出反馈，并把整个项目迁移到另一台设备继续使用。

当前推荐入口是 **TREE 桌面 App**；CLI 保留给自动化、调试和高级用户。当前源码版本与最新正式版本均为 `0.3.7`。

## 当前状态

| 项目 | 当前实现 |
| --- | --- |
| 桌面架构 | Tauri 原生壳加载 React 前端，并为当前项目启动一个本机、令牌保护的 Python engine sidecar。 |
| 项目管理 | 桌面 App 以项目为中心；受管理项目保存在 `~/.tree/projects/`，可创建、导入、重命名、导出、迁移和删除。 |
| 生成与学习 | 支持资料导入、AES PDF、大资源 PDF/OCR、清洗/切分/RAG、知识图谱、生成 Markdown 学习文件、阅读推荐、完成标记和基于反馈的定向修订。七阶段支持累计进度、断点续跑和局部失败。 |
| 运行环境 | LLM 与 PaddleOCR 可在 App 中配置；本地 embedding 扩展会在需要时安装运行时和模型，也可改用外部 OpenAI-compatible embedding endpoint。 |
| 已发布安装包 | macOS Apple Silicon DMG，以及 Windows x64 NSIS 和 MSI 安装包。 |

## 下载

当前发布版本：[v0.3.7](https://github.com/Waylon524/tree/releases/tag/v0.3.7)

| 平台 | 安装包 | 说明 |
| --- | --- | --- |
| macOS Apple Silicon | [TREE_0.3.7_macos.dmg](https://github.com/Waylon524/tree/releases/download/v0.3.7/TREE_0.3.7_macos.dmg) | 已使用 Developer ID 签名、公证并 stapled。 |
| Windows x64 | [TREE_0.3.7_x64-setup.exe](https://github.com/Waylon524/tree/releases/download/v0.3.7/TREE_0.3.7_x64-setup.exe) | NSIS 安装包。Windows 可能因新应用信誉不足显示 SmartScreen 提醒。 |
| Windows x64 | [TREE_0.3.7_x64_en-US.msi](https://github.com/Waylon524/tree/releases/download/v0.3.7/TREE_0.3.7_x64_en-US.msi) | MSI 安装包，适合偏好 Windows Installer 的场景。 |

SHA-256：

```text
TREE_0.3.7_macos.dmg          ab8517466dcf82ef81330decfddef31ad402fda7618ae1e71ffe47027022a97e
TREE_0.3.7_x64-setup.exe      e90247f6b942e07afb44455280d89409c0032f2c5db63182374f675c522fde57
TREE_0.3.7_x64_en-US.msi      a3b8430d23e78925c254a19f76bc2c0aa7c67d07f1567eac07fc4364df880651
```

## TREE 是什么

TREE 的目标不是简单总结资料，而是把一批学习材料整理成可以持续阅读、修订和迁移的学习项目。

它会做这些事：

- **采集资料**：导入 PDF、PPT/PPTX、Word、Markdown、文本和图片。
- **抽取与清洗内容**：对扫描件和图片资料做 OCR，对抽取结果做清洗和语义切分。
- **建立知识图谱**：把可学习的知识点变成节点，并建立先修依赖关系。
- **生成学习文件**：为每个知识节点生成一份 Markdown 学习笔记。
- **推荐学习顺序**：根据知识点依赖关系推荐当前适合阅读的节点，但不强制顺序。
- **记录阅读进度**：已读、正在阅读、推荐阅读、未读会在知识图谱里以不同颜色显示。
- **根据反馈微调节点**：如果某个节点讲得不清楚，可以提交反馈，让 Writer 对当前学习文件做一次定向修订。
- **迁移整棵树**：导出项目 zip，在另一台设备上导入后继续生成或继续阅读。

适合的使用场景包括：教材章节拆解、课程讲义整理、考试复习资料整合、扫描课件 OCR 后结构化学习，以及把多个来源的课程材料变成统一的知识图谱。

## App 页面

### 果园 Orchard

果园是项目库。每个项目是一棵果树。

- **Plant / From Seeds**：新建一棵空果树。
- **Plant / From Parent Tree**：从 TREE 项目 zip 导入一棵已有果树。
- **Propagate**：复制一份项目 zip，用于备份或迁移，不删除本地项目。
- **Transplant**：导出项目 zip，导出成功后从本机果园中移除该项目。
- **Uproot**：只删除本机项目，不导出。

项目 zip 会包含材料、生成结果、运行状态、知识图谱、NodeRun 进度和阅读进度。`.env`、`.tree/config.env`、全局 `~/.tree/config.env` 等密钥配置不会被打包，所以换设备后需要在新设备上重新配置 API Key。

### 照料 Tend

照料页用于准备项目和配置运行环境。

- 导入或移除资料。
- 配置 LLM API、Base URL、provider profile、默认模型和角色模型。
- 配置 PaddleOCR token、API URL 和 OCR 模型。
- 调整运行参数，例如 llama-server context 和 MTU chunk 阈值。
- 在高级设置中调整 NodeRun、LLM retry/timeout、Source/OCR、Archivist 和 Dagger 参数。
- 在 Agent 提示词区查看、修改或恢复 Examiner、Student、Writer、Archivist 和 Dagger 的内置 prompt。
- 切换界面语言。

普通用户通常只需要为 LLM、PaddleOCR 和 embedding 准备好运行环境，再开始生成。运行参数和密钥保存在本机全局 `~/.tree/config.env`；Agent prompt override 保存在当前项目的 `.tree/prompts/overrides.json`，会随 Parent Tree zip 一起迁移。

### 生长 Grow

生长页是流水线控制台。点击 **Run** 后，TREE 会依次推进：

| 阶段 | 作用 |
| --- | --- |
| Gather / 采集 | 从资料中抽取文本，必要时调用 OCR。 |
| Clean / 筛净 | 清洗 OCR 和抽取噪声，保留教学内容。 |
| Cut / 分种 | 将清洗后的文本切成可教学的语义单元 MTU。 |
| Embed / 播种 | 将语义单元写入 RAG 索引。 |
| Cluster / 发芽 | 将相关 MTU 聚合成知识节点。 |
| Link / 生枝 | 建立知识节点之间的先修依赖。 |
| NodeRun / 结果 | 为每个知识节点生成最终学习文件。 |

Run 支持续跑。进度条始终显示项目累计完成量；缓存命中和重启后会继承真实的 `done/total`，与一次性跑完保持相同显示，不使用“已复用”或 `0/0 = 100%` 的伪进度。并行 PDF OCR 按每个分块的单调已完成页数汇总，乱序、重复、重试和结果复用事件都不会让文件进度倒退或提前完成。阶段失败时，TREE 会冻结一致的终止快照，后台迟到事件不会把页面重新标成“进行中”；再次 Run 会从保留检查点继续。主动暂停会取消尚未结束的 NodeRun 请求并把 CLI、GUI 和阶段 active 统一显示为 stopped，但仍保留试卷、草稿、迭代和 `in_progress` 检查点供下次续跑。单个 NodeRun 失败时，已完成结果继续可用，项目显示为部分完成，并可从保留的试卷、草稿和 Bottleneck 精准重试。

TREE 会按实际 LLM 服务商共享并发预算，遇到 429、超时、服务繁忙和可恢复 5xx 时自动降速并遵守 `Retry-After`。每次 AI 请求都有稳定的 operation id，并按具体任务选择输出上限、超时、推理模式、JSON 能力和重试次数；角色级配置仍是用户可调的总上限。项目级 `.tree/runtime/services/llm-operations.jsonl` 以有界轮转 JSONL 记录 operation、角色、provider、模型、token 估算与实际 usage、耗时、重试原因、终止原因和降级状态，但不会记录 prompt、材料、学生答案、模型正文、密钥或 Authorization header；`tre logs` 可列出该文件，GUI 诊断接口可读取最近摘要。

模型返回在进入业务逻辑前会验证完整响应契约；Archivist 和 Dagger 的 JSON 经过严格 schema，输出截断、内容过滤、拒答、意外 tool call 和空响应具有不同终态。所有 Agent 都把代码声明的任务控制与 OCR、RAG、草稿、反馈等不可信材料分区，材料中的提示词不能改写当前任务。Archivist Clean 会先移除 OCR 图片噪声，再按“最多 1000 行且最多 100000 字符”拆分请求并优先保留标题边界；若较小窗口仍因输出上限截断，会递归二分，最小窗口持续失败时保守保留原文而不静默删除教学内容。Archivist 只抽取 MTU，Dagger 独占 MTU 归组和节点边界；历史 `excercise` 会兼容读取并规范为 `exercise`。Dagger 只有在模型显式选择 `selected` 或 `none` 后才接受先修结论，同名 define 只表示需要检查而不自动合并；大批量节点输出超限时会递归拆批，环修复只能最小删除当前报告环上的依赖，不能改写无关边或强制多根、并行分支串行化。

Examiner 交给 Writer 的指令会先解析为严格结构，硬约束始终高于指令、草稿、RAG、Bottleneck 和用户反馈等动态内容；即时 `EXAM_DEFECT` 会携带缺陷类型和真实迭代上下文进入独立复核，复核确认试卷无误时继续 Writer 教学循环，而不是把 `KEEP_FAIL` 误当成节点终止。真正属于图谱而非当前 Writer 的缺失先修会以 `PLANNER_DEFECT: MISSING_PREREQUISITE` 明确终止当前节点并提示重新生长，不再被误修成越界正文。材料外基础不会被假定为已掌握，Writer 必须在当前节点补足最小解释桥；材料内先修与来源引用只允许使用代码提供的节点关系和证据路径。Dagger 固定的成员 MTU 与 defines 必须被综合覆盖，RAG 分块本身不被误当成节点边界。反馈修订沿用相同 Writer 教学契约，程序会确定性保留 H1、先修前置和来源追溯区块。每次 Examiner 复核的触发原因、动作和模型理由都会保留在 NodeRun 状态中。Embed 会定向修复缺失 MTU，Link 模型修复耗尽后会按可审计规则移除最低置信边，最终 DAG 仍保持无环。

本地 PDF 页数检查与 OCR 分块会在各自作用域内汇总 pypdf 已自动恢复的缺失对象和重复 `/Filter` 警告，避免重复噪声淹没诊断；密码、crypto 依赖、非法超长流、缺页和无法读取等完整性错误仍会明确失败。

枯果的 **重新生长** 与普通断点恢复语义不同：重新生长会清空旧试卷、草稿引用、迭代和 Bottleneck 历史，从新试卷开始；普通再次 Run 则保留检查点继续。

### 收获 / 知识图谱

原来的 DAG 页面现在是学习入口。生成未完成时，它仍然显示生成状态；当所有知识点文件生成完成后，3D 图会切换为阅读状态：

- 未读节点：浅绿色。
- 推荐阅读或正在阅读：深绿色。
- 已读节点：棕色。
- 锁定节点：灰色。
- 生成失败节点：红色。

推荐规则是确定性的：优先推荐“前置节点已经读完、自己还没读”的节点。推荐只是提示，你仍然可以自行选择任意已生成节点阅读。

### Reader 阅读器

Reader 用于阅读生成的 Markdown 学习文件，支持公式、表格和常见 Markdown 内容。

打开知识图谱中的节点会记录阅读状态。读完后点击完成阅读，该节点会在知识图谱中变为已读。

如果某个节点讲解不清楚，可以在 Reader 中提交反馈。V1 不会重跑完整 Examiner / Student / Writer 循环，而是只把你的反馈交给 Writer，对当前学习文件做最小必要修改。修订成功后，TREE 会备份旧文件、覆盖当前输出，并重新索引该节点；修订失败则不会覆盖原文件。

### 果实 Fruits

果实页列出所有已经生成的学习文件。你可以搜索、打开、导出选中的文件，或一次性导出全部生成结果。

## 推荐工作流

1. 从 Release 页面下载并安装桌面 App。
2. 打开果园，选择 **Plant / From Seeds** 新建项目。
3. 在照料页导入资料，并配置 LLM / OCR。
4. 在生长页点击 **Run**，等待知识图谱和学习文件生成。
5. 在知识图谱页从推荐节点开始学习，也可以自行选择节点。
6. 在 Reader 中阅读、标记完成，必要时提交反馈让 Writer 微调。
7. 在果实页导出学习文件，或用 Propagate 导出整棵项目树。

## 运行依赖

TREE 是本地桌面 App，但运行时需要模型和 OCR 服务：

- **LLM API**：需要 OpenAI-compatible Chat API。可在照料页配置。
- **PaddleOCR**：用于扫描 PDF、图片和复杂版式资料。
- **PDF 运行时**：安装包内置 `pypdf` AES 支持；无需密码的 AES PDF 可直接读取，需要密码的文件会显示文件名和处理建议。包含超过 pypdf 默认 75 MB 单流阈值的本地 PDF 会按文件实际大小受控读取后再分块，其他解压与图片安全限制保持启用。
- **Embedding**：桌面 App 可使用本地 embedding 扩展，基于 `llama-server` 和 Qwen3 embedding GGUF 模型。
- **本地存储**：受管理项目保存在 TREE 项目库中；项目迁移使用 zip archive。

当前 macOS 安装包面向 Apple Silicon。Windows 安装包面向 x64。

## 高级设置

高级参数建议优先在桌面 App 的照料页修改。配置会写入全局 `~/.tree/config.env`。

常见参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `LLAMA_SERVER_CTX` | `22000` | llama-server 上下文长度。UI/API 限制为 `1024..32768`。Stop/Run 或重启 embedding 后生效。 |
| `SOURCE_MTU_CHUNK_TOKENS` | `20000` | Source MTU chunk 阈值。UI/API 限制为 `500..32768`。 |
| `LLM_PROVIDER_CONCURRENCY` | `4` | 同一 LLM 端点与凭据共享的并发上限；限流时会自动降低。 |
| `LLM_PROVIDER_PROFILE` | `auto` | 能力 profile：`auto`、`deepseek`、`openai` 或 `generic`；未知兼容端点自动采用 `generic`，不发送 DeepSeek 专属参数。 |
| `LLM_CONTEXT_WINDOW` | `1000000` | 每个角色默认的总上下文预算，与默认 DeepSeek V4 Flash 的 1M 上下文一致；更换 provider/model 时必须改为其真实限制。 |
| `LLM_MAX_OUTPUT_TOKENS` | `131072` | 从 context window 中预留的角色级最大输出 token；operation 会按任务使用 `8192`、`32768`、`65536` 或 `131072` 的更小上限。 |
| `LLM_PROMPT_SAFETY_TOKENS` | `1024` | 为 tokenizer 估算误差和消息封装保留的安全余量。 |
| `SOURCE_INGEST_CONCURRENCY` | `4` | 并行处理的材料数。 |
| `ARCHIVIST_CHUNK_CONCURRENCY` | `2` | 单次材料清洗/切分的并行分块数。 |
| `DAGGER_PREREQUISITE_CONCURRENCY` | `3` | Dagger 先修关系请求并发数。 |
| `MAX_ACTIVE_NODE_RUNS` | `3` | 同时运行的 NodeRun 数。 |
| `PADDLEOCR_MODEL` | `PaddleOCR-VL-1.6` | PaddleOCR 服务使用的模型名。 |
| `PADDLEOCR_API_URL` | `https://paddleocr.aistudio-app.com/api/v2/ocr/jobs` | PaddleOCR 请求地址。 |
| `EMBED_API_URL` | 本地 llama-server endpoint | 使用外部 embedding 服务时可覆盖。 |
| `EMBED_AUTO_START` | App 管理 | 如果你自己管理 embedding 服务，可设为 `false`。 |
| `LLAMA_SERVER_BIN` | App 管理 | 指定自定义 `llama-server` 可执行文件。 |

照料页还开放了更多高级运行参数，包括 `MAX_ITERATIONS`、`MAX_EXAMINER_SPAN_NODES`、`MAX_RETRIES`、`MAX_FORMAT_RETRIES`、`LLM_TIMEOUT_SEC`、token 预算、`SOURCE_OCR_CONCURRENCY`、`SOURCE_EMBEDDING_CONCURRENCY`、`ARCHIVIST_MTU_REPAIR_ATTEMPTS`、`DAGGER_REPAIR_ATTEMPTS` 和 Dagger cluster 相关阈值。界面默认值与引擎一致，保存后会说明下次运行需要重算的最早阶段；普通项目建议保留自适应并发的默认设置。

每个角色都可以通过 `<ROLE>_PROVIDER_PROFILE`、`<ROLE>_CONTEXT_WINDOW` 和 `<ROLE>_MAX_OUTPUT_TOKENS` 覆盖全局值，例如 `DAGGER_CONTEXT_WINDOW=160000`。调用前 TREE 会估算 system + user 输入；operation 的输出预算只会在角色上限以内收紧，短 JSON 修复不会默认占用与长文生成相同的输出额度和推理强度。超出输入预算时会先缩减低优先级 RAG/repair 上下文或为 Dagger 覆盖输入分批，仍无法容纳时在请求 provider 前返回包含 operation、角色和预算数字的错误。

Agent prompt 是项目级设置。修改 prompt 会影响 JSON 格式稳定性、知识图谱生成和审核结果；每个 prompt 都可以单独恢复默认，也可以一键全部恢复默认。有效的 Archivist/Dagger prompt 哈希和语义配置会进入对应 Planner 缓存签名，因此只失效真正受影响的阶段，不包含 API key 或 prompt 正文。

`SOURCE_MTU_CHUNK_TOKENS` 不强制小于 `LLAMA_SERVER_CTX`，因为 TREE 的 token 估算和 llama.cpp 实际 tokenization 不完全一致。默认组合是 `LLAMA_SERVER_CTX=22000`、`SOURCE_MTU_CHUNK_TOKENS=20000`。

## CLI 与开发者用法

普通用户建议使用桌面 App。CLI 主要用于自动化、调试、开发和无界面环境。

从源码安装 CLI：

```bash
pipx install "tree-engine[rag,gui] @ git+https://github.com/Waylon524/tree.git"
```

常用命令：

```bash
tre setup
tre gui --no-browser
tre embedding start
tre embedding stop
```

开发与验证需要 Python 3.12、Node.js 20.19+（或 22.12+）和 Rust。Python 依赖使用发布约束文件安装；前端使用 lockfile 安装：

```bash
python3 -m pip install -c packaging/release-constraints.txt -e ".[rag,gui,dev]"
python3 -m ruff check tree_engine tests
packaging/test_local.sh -q

cd desktop
npm ci
npm test
npm run build
npm run tauri dev
```

Tauri shell 的 Rust 测试和格式检查在 `desktop/src-tauri/` 下运行：

```bash
cargo fmt --check
cargo test --locked
```

## 项目数据与隐私

TREE 项目 zip 是迁移和备份格式，不是密钥备份格式。它会包含项目材料、生成结果、运行中间产物、RAG store、NodeRun 状态和学习状态，但会排除 `.env`、`.tree/config.env`、全局 `~/.tree/config.env`、服务日志、pid 文件和其他易失运行状态。

这意味着你可以通过 **Plant / From Parent Tree** 在另一台设备继续项目，但目标设备仍然需要配置自己的 LLM / OCR 凭据。

TREE 会把完成任务所需的材料内容发送到你主动配置的 LLM 和 OCR 服务。具体的数据保留、训练和跨境处理规则由相应服务提供商决定；导入含有个人、学校或机构敏感信息的资料前，请先确认所选服务符合你的隐私要求。TREE 的项目迁移包不会包含 API 密钥，但材料、生成结果和运行状态本身仍可能包含敏感内容，请像保护原始课程资料一样保管迁移包。

## 项目文档

| 文档 | 职责 |
| --- | --- |
| [README.md](README.md) | 面向使用者说明项目当前状态、能力、使用方式和已发布版本。 |
| [PLAN.md](PLAN.md) | 记录讨论后已确认、但尚未完成的未来计划。 |
| [CHANGELOG.md](CHANGELOG.md) | 按时间记录每次项目变更。 |

历史产品化与打包决策分别保存在 [PRODUCTIZATION_PLAN.md](PRODUCTIZATION_PLAN.md) 和
[PACKAGING_PLAN.md](PACKAGING_PLAN.md)，它们不再作为当前待办清单。汇报与海报源文件见
[poster/README.md](poster/README.md)。具体维护规则见 [AGENTS.md](AGENTS.md)。

## License

MIT
