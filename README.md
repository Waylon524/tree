# TREE

TREE 是一个桌面学习工作台。它可以把 PDF、课件、Word、图片、Markdown 等课程资料整理成一棵“知识果树”：先采集和清洗资料，再生成知识图谱和每个知识点的学习文件，最后让你在 App 里按依赖关系学习、标记阅读进度、对讲解不清楚的节点提出反馈，并把整个项目迁移到另一台设备继续使用。

当前推荐入口是 **TREE 桌面 App**。CLI 仍然保留给自动化、调试和高级用户，但不再是普通使用者的主要入口。

## 下载

当前发布版本：[v0.3.6](https://github.com/Waylon524/tree/releases/tag/v0.3.6)

| 平台 | 安装包 | 说明 |
| --- | --- | --- |
| macOS Apple Silicon | [TREE_0.3.6_macos.dmg](https://github.com/Waylon524/tree/releases/download/v0.3.6/TREE_0.3.6_macos.dmg) | 已使用 Developer ID 签名、公证并 stapled。 |
| Windows x64 | [TREE_0.3.6_x64-setup.exe](https://github.com/Waylon524/tree/releases/download/v0.3.6/TREE_0.3.6_x64-setup.exe) | NSIS 安装包。Windows 可能因新应用信誉不足显示 SmartScreen 提醒。 |
| Windows x64 | [TREE_0.3.6_x64_en-US.msi](https://github.com/Waylon524/tree/releases/download/v0.3.6/TREE_0.3.6_x64_en-US.msi) | MSI 安装包，适合偏好 Windows Installer 的场景。 |

SHA-256：

```text
TREE_0.3.6_macos.dmg          f6ab148d059b254b80c4596a20ee950fe9053672d22feda02fedda13ae611db6
TREE_0.3.6_x64-setup.exe      7517774498dc7f1f6889e366e51fa4729a3ecda92debff11567aae9b3fe32706
TREE_0.3.6_x64_en-US.msi      a6fa77225a8fdb9178aa377959514841e1f7a4d995072b7a558f30874bd7b019
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
- 配置 LLM API、Base URL、默认模型和角色模型。
- 配置 PaddleOCR token、API URL 和 OCR 模型。
- 调整运行参数，例如 llama-server context 和 MTU chunk 阈值。
- 在高级设置中调整 NodeRun、LLM retry/timeout、Source/OCR、Archivist 和 Dagger 参数。
- 在 Agent 提示词区查看、修改或恢复 Examiner、Student、Writer、Archivist 和 Dagger 的内置 prompt。
- 切换界面语言。

普通用户通常只需要配置一次 LLM 和 PaddleOCR。运行参数和密钥保存在本机全局 `~/.tree/config.env`；Agent prompt override 保存在当前项目的 `.tree/prompts/overrides.json`，会随 Parent Tree zip 一起迁移。

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

Run 支持续跑。如果材料没有变化，并且已有知识图谱和中间产物可读，TREE 会复用已有结果并继续 NodeRun，而不是每次都重新构建 Cluster 和 Link。

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
| `PADDLEOCR_MODEL` | `PaddleOCR-VL-1.6` | PaddleOCR 服务使用的模型名。 |
| `PADDLEOCR_API_URL` | `https://paddleocr.aistudio-app.com/api/v2/ocr/jobs` | PaddleOCR 请求地址。 |
| `EMBED_API_URL` | 本地 llama-server endpoint | 使用外部 embedding 服务时可覆盖。 |
| `EMBED_AUTO_START` | App 管理 | 如果你自己管理 embedding 服务，可设为 `false`。 |
| `LLAMA_SERVER_BIN` | App 管理 | 指定自定义 `llama-server` 可执行文件。 |

照料页还开放了更多高级运行参数，包括 `MAX_ITERATIONS`、`MAX_ACTIVE_NODE_RUNS`、`MAX_EXAMINER_SPAN_NODES`、`MAX_RETRIES`、`MAX_FORMAT_RETRIES`、`LLM_TIMEOUT_SEC`、`SOURCE_INGEST_CONCURRENCY`、`SOURCE_OCR_CONCURRENCY`、`SOURCE_EMBEDDING_CONCURRENCY`、`ARCHIVIST_MTU_REPAIR_ATTEMPTS`、`DAGGER_REPAIR_ATTEMPTS` 和 Dagger cluster 相关阈值。保存后通常需要 Stop/Run 或重启服务才会完全生效。

Agent prompt 是项目级设置。修改 prompt 会影响 JSON 格式稳定性、知识图谱生成和审核结果；每个 prompt 都可以单独恢复默认，也可以一键全部恢复默认。

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

桌面端开发：

```bash
cd desktop
npm install
npm run dev
npm run build
```

Python runtime 测试：

```bash
pytest
```

## 项目数据与隐私

TREE 项目 zip 是迁移和备份格式，不是密钥备份格式。它会包含项目材料、生成结果、运行中间产物、RAG store、NodeRun 状态和学习状态，但会排除 `.env`、`.tree/config.env`、全局 `~/.tree/config.env`、服务日志、pid 文件和其他易失运行状态。

这意味着你可以通过 **Plant / From Parent Tree** 在另一台设备继续项目，但目标设备仍然需要配置自己的 LLM / OCR 凭据。

## License

MIT
