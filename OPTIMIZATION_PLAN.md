# T.R.E.E. 系统优化清单

> 版本: v2.3 | 日期: 2026-05-27 | 分支: main
> 变更：v1.0→v2.0：移除本地模型，全在线 + Vectorize RAG
> 变更：v2.0→v2.1：M4 改回 Markdown；新增草稿实时索引、Prompt Caching、可观测性、API 容错
> 变更：v2.1→v2.2：模型代称还原为实际模型(DeepSeek V4)；新增 M6 独立平台
> 变更：v2.2→v2.3：M1 PaddleOCR-VL 改为 API 客户端，删除本地 PaddlePaddle/PaddleOCR 依赖

---

## 总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                     M1 · 资料摄入与预处理                            │
│  PaddleOCR-VL v1.5（API 客户端）+ 在线 DeepSeek V4 Flash（结构化整理）│
│  用户资料 → 类型判断 → 文本提取/OCR → 在线 LLM 结构化 → Markdown      │
│  输出: /source_materials/<chapter>/*.md                            │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│              M2 · Examiner 提示词重构 + 瘦身                        │
│  在线 DeepSeek V4 Pro · 读资料 → 细粒度命题                         │
│  为 Student 输出结构化答题指令，为 Architect 输出创作指令              │
│  删除 Agent Memory 模板（~130 行）                                   │
└──────────┬───────────────────────────────┬───────────────────────┘
           ▼                               ▼
┌───────────────────────────┐   ┌─────────────────────────────────┐
│  M3 · Student 提示词适配    │   │  M4 · Architect Markdown + 适配  │
│  在线 DeepSeek V4 Flash   │   │  在线 DeepSeek V4 Flash         │
│  全新启动，预读协议          │   │  Markdown + LaTeX 输出           │
│  删除 Memory 模板（~135行） │   │  删除 Memory 模板（~135行）       │
└───────────────────────────┘   └─────────────────────────────────┘
           │                               │
           └───────────────┬───────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│           M5 · RAG 知识索引 + Prompt Cache + 可观测性               │
│  Cloudflare Vectorize + Workers query endpoint                   │
│  草稿实时索引 · 章节账本 · 语义检索替代全文注入                       │
│  NotebookLM 式 grounding + auto-briefing                         │
│  Prompt Caching · 迭代上限 · API 容错 · 结构化日志                  │
└──────────────────────────────────────────────────────────────────┘
```

### 角色分配

| 角色 | 模型 | 位置 | 原因 |
|------|------|------|------|
| Ingest Pipeline（资料处理） | PaddleOCR-VL v1.5 API + DeepSeek V4 Flash | 在线 | OCR 服务远程部署（GPU 加速），客户端通过 HTTP 调用；结构化整理走在线 LLM |
| Examiner（考官） | DeepSeek V4 Pro | 在线 | 最强推理能力，命题+审计不可降级 |
| Student（学生） | DeepSeek V4 Flash | 在线 | 模拟零基础学生——Flash 级模型不会"太聪明"，刚好 |
| Architect（建筑师） | DeepSeek V4 Flash | 在线 | 按 Markdown 模板生成，Examiner 给出结构约束后可胜任 |

> **模型代称映射**：文档中 "Opus" = DeepSeek V4 Pro，"Haiku" = DeepSeek V4 Flash。当前在 Claude Code 中运行时使用 Claude 模型代称，M6 独立平台将直接调用 DeepSeek API。

### 执行顺序与依赖

```
M1（资料摄入）──→ M2（Examiner 重构）──┬──→ M3（Student 适配）
                                       └──→ M4（Architect Markdown + 适配）
M5（RAG + Cache + 可观测性）←───────────────────┘  待 M2-M4 稳定后实施
M6（独立平台）←────────────────────────────────────────── M1-M5 全部完成后
```

**关键路径：M1 → M2 → M3/M4 → M5 → M6**。M2 的指令模板是 M3 和 M4 的输入契约，必须先行。M5 依赖 M2-M4 的 agent 接口稳定后才能设计 RAG 查询协议。M6 依赖 M1-M5 全部就绪后才能移植。

---

## M1 · 资料摄入与预处理流水线

**难度：** ⭐⭐⭐ 中 | **人力：** 1 人 | **依赖：** 无

### 目标

接收用户放入 `raw_materials/` 的任意格式资料，使用远程 OCR API + 在线 LLM 完成类型判断、文字提取、结构化整理，输出标题齐全、顺序合理、无乱码、无教学无关内容的完整 Markdown 到 `source_materials/<chapter>/`。

### 技术栈

| 组件 | 选型 | 位置 | 用途 |
|------|------|------|------|
| OCR + 公式识别 | **PaddleOCR-VL v1.5 API** | 远程服务 | 统一 OCR + 公式识别，客户端通过 HTTP 调用远程服务 |
| PDF 渲染 | **PyMuPDF (fitz)** | 本地 | 将 PDF 页渲染为图片供 PaddleOCR-VL API 处理 |
| Word 解析 | **python-docx** | 本地 | 提取 .docx 文本（含表格）+ 内嵌图片导出 |
| 图片预处理 | **Pillow** | 本地 | 图片格式转换与基本处理 |
| HTTP 客户端 | **httpx** | 本地 | 调用远程 PaddleOCR-VL API |
| 结构化整理 | **DeepSeek V4 Flash** | 在线 API | 标题识别、乱码修复、无关内容过滤、章节排序 |

### 输入

- `raw_materials/` 下任意文件：PDF、图片（PNG/JPG/TIFF）、Word（.docx）、纯文本（.txt/.md）

### 处理流程

```
raw_materials/
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 1: 类型判断（MIME sniffing + 扩展名）                    │
│  - PDF  → PDF Extractor                                     │
│  - .docx → python-docx Extractor                            │
│  - .png/.jpg/.tiff → OCR Pipeline                           │
│  - .txt/.md → 直接读取                                       │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 2: 文字提取（全部走 PaddleOCR-VL v1.5 API）               │
│                                                              │
│  PDF 文件：                                                   │
│  1. Base64 编码 → POST /layout-parsing (fileType=0)           │
│  2. 远程服务：版面分析 + OCR + 公式 → LaTeX → Markdown          │
│                                                              │
│  纯图片：                                                      │
│  1. Base64 编码 → POST /layout-parsing (fileType=1)           │
│  2. 远程服务：OCR + 公式识别 → Markdown                         │
│                                                              │
│  Word：                                                       │
│  1. python-docx 提取段落 + 表格文字                             │
│  2. 内嵌图片 → 导出 → Base64 → POST /layout-parsing           │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 3: PaddleOCR-VL v1.5 API 识别                           │
│                                                              │
│  远程服务配置：                                                 │
│  - 流水线版本: v1.5（最佳公式识别）                              │
│  - 服务端点: POST /layout-parsing                             │
│  - 请求格式: JSON { file: base64, fileType, ... }             │
│  - 响应格式: JSON { result: { layoutParsingResults: [...] } } │
│  - PDF 多页: restructurePages=true 自动合并                    │
│  - 版面检测/方向分类/展平: 服务端配置                            │
│  - 公式识别: 内置（自动输出 LaTeX）                             │
│                                                              │
│  输出：每页 Markdown 文本（含 LaTeX 公式）                      │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 4: 结构化整理（在线 DeepSeek V4 Flash）                    │
│                                                              │
│  将 Step 2+3 提取的全部原始文本送入在线 LLM，指令：              │
│  1. 识别并提取文档标题、章节标题（基于编号、字号标记）              │
│  2. 去除页眉/页脚/水印/版权声明/广告                              │
│  3. 修复 OCR 常见错误（形近字、标点、公式符号）                    │
│  4. 合并跨页断裂的段落                                          │
│  5. 去除与教学内容无关的段落                                     │
│  6. 按逻辑顺序重排章节                                          │
│  7. 保持公式原貌（LaTeX 格式）                                  │
│                                                              │
│  输出：干净的 Markdown，标题层级正确，无冗余内容                    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
source_materials/<chapter>/*.md
```

### PaddleOCR-VL v1.5 服务部署

```bash
# 方式 1: 本地启动服务（需要 GPU 服务器）
pip install "paddleocr[serve]"
paddlex --serve --pipeline PaddleOCR-VL --port 8080

# 方式 2: Docker 部署（推荐，含 GPU 支持）
docker compose up  # 使用官方 compose.yaml + .env

# 方式 3: 远程托管服务
# 将 API URL 配置为远程端点即可

# 客户端配置
export OCR_API_URL="http://localhost:8080"  # 或远程 URL
pip install httpx PyMuPDF python-docx Pillow openai

# 验证连接
python3 -c "
from ingest.ocr_engine import get_engine
engine = get_engine(api_url='http://localhost:8080')
print('OCR API client connected')
"
```

### 结构化整理 — 在线 LLM Prompt 设计

```
你是一位文档结构化专家。请对以下 OCR 提取的原始文本进行处理：

1. 识别并标记标题层级（# ## ###）
2. 删除所有页眉、页脚、页码、水印、版权声明
3. 删除广告、推广链接、与教学无关的段落
4. 修复 OCR 错误：
   - 常见形近字错误（已/己/巳, 未/末, 日/曰）
   - 标点错误（半角→全角，缺失标点补全）
   - 数学符号错误（→ 修复为正确 LaTeX）
5. 合并跨页断裂的段落
6. 保持公式原貌（用 $...$ 或 $$...$$ 包裹）
7. 按逻辑顺序排列章节

原始文本：
{raw_text}
```

### 产出物

| 文件 | 说明 |
|------|------|
| `ingest/pipeline.py` | 主编排脚本，串联全部 Step |
| `ingest/extractors/pdf_extractor.py` | PDF → PaddleOCR-VL v1.5 API（全 OCR，无 PyMuPDF 文本捷径） |
| `ingest/extractors/docx_extractor.py` | python-docx 提取（含内嵌图片导出 → PaddleOCR-VL API） |
| `ingest/extractors/image_extractor.py` | 图片 → PaddleOCR-VL v1.5 API |
| `ingest/ocr_engine.py` | PaddleOCR-VL v1.5 API 客户端（HTTP、单例、PDF/图片接口、Markdown 输出） |
| `ingest/structurer.py` | 调用在线 LLM API 进行结构化整理 |
| `ingest/requirements.txt` | Python 依赖清单 |
| `ingest/prompts/structurer.txt` | 结构化整理 Prompt 模板 |

### 验收标准

- [ ] PaddleOCR-VL v1.5 服务可访问，API 客户端连接成功
- [ ] PDF 全 OCR 提取率 ≥ 95%（含公式）
- [ ] PaddleOCR-VL v1.5 中文识别准确率 ≥ 90%
- [ ] 公式识别输出为 LaTeX 格式，准确率 ≥ 85%
- [ ] 混合 PDF（部分文字 + 部分扫描图片）全部内容被提取
- [ ] Word 内嵌图片文字被正确 OCR
- [ ] 输出 Markdown 标题层级正确，无乱码
- [ ] 页眉/页脚/水印/广告被移除
- [ ] 单文件端到端处理 < 60 秒（10MB 混合 PDF，GPU 服务模式）
- [ ] 在线 LLM 结构化整理后人工复核通过率 ≥ 85%

---

## M2 · Examiner 提示词重构 + 瘦身

**难度：** ⭐⭐⭐⭐ 高 | **人力：** 1-2 人 | **依赖：** M1 完成

### 目标

重写 `faithfulness-examiner.md`，使其：① 读取 M1 整理后的资料选择知识点命题；② 为 Student 输出极其细致的答题指令；③ 为 Architect 输出精准的 Markdown 创作指令；④ 删除末尾 Agent Memory 模板。

### 当前问题

- `faithfulness-examiner.md` 行 202-336（~135 行）为 Persistent Agent Memory 模板，子智能体不需要跨会话记忆
- 当前只输出 `## [Blind_Exam]` + `## [Answer_Key]`，无下游结构化指令
- 未适配 Flash 级模型的能力边界

### 具体改动

#### 2.1 删除 Agent Memory 模板

删除文件末尾 `# Persistent Agent Memory` 及其后全部内容（行 202-336）。

保留的内容止于 `## Quality Safeguards` 末尾。

#### 2.2 新增：读取 source_materials

在 Phase A 开头增加步骤 0：

```
0. Read source_materials/<chapter>/*.md to understand available source content.
   Knowledge points must be grounded in source materials, not invented.
```

#### 2.3 新增：为 Student 输出结构化答题指令

在 `## [Blind_Exam]` 之后新增 `## [Student_Instructions]`：

```markdown
## [Student_Instructions]
### 答题格式要求
- 每道题必须严格按 Part A/B/C/D 四段式作答
- Part A 每条例证必须用 [Evidence N]: "exact quote" (from <file>, Section X) 格式
- Part B 每一步推导必须标注来源：[Evidence N] 或 [Prior Draft: filename.md]
- 无法推导时必须立即在 Part C 声明 [! Logic Gap] 并停止

### 本题特定约束
- Q1: 允许使用 [具体前序文件] 中的 [具体概念]
- Q2: 禁止使用 [某类推理]，必须从草稿中寻找公式
- ...
```

#### 2.4 新增：为 Architect 输出 Markdown 创作指令

在 `## [Answer_Key]` 之后新增 `## [Architect_Instructions]`：

```markdown
## [Architect_Instructions]
### Markdown 结构要求
- 使用标准 Markdown + LaTeX（$...$ inline，$$...$$ display）
- 整体结构：头部元信息 → 学习目标 → 前置知识 → 核心内容 → 例题 → 常见误区 → 自测题
- 长推导使用折叠标记：`> [!details] 完整推导` 后缩进内容
- 引用前序文件使用：`[概念名](filename.md#section)`

### 内容范围约束
- 必须覆盖的知识缺陷：[列出具体缺陷]
- 禁止涉及的知识点：[列出边界]
- 预计规模：< 500 行 Markdown

### 引用约束
- 所有引用前序文件的概念须标注来源：`[概念名](filename.md#section)`
```

#### 2.5 新增 `ROUTE:` 机器可解析路由

在 Bottleneck Report 末尾增加标准化路由：

```
ROUTE: PASS|FAIL_KNOWLEDGE_GAP
EXAM_ID: <exam_id>
```

### 产出物

| 文件 | 说明 |
|------|------|
| `.claude/agents/faithfulness-examiner.md` | 重写后的 Examiner |

### 验收标准

- [ ] 文件总行数从 335 降到 ~220（含新增指令模板）
- [ ] `## [Student_Instructions]` 包含格式约束 + 本题特定约束
- [ ] `## [Architect_Instructions]` 包含 Markdown 结构要求 + 内容范围约束 + 引用约束
- [ ] `ROUTE:` 可被总场控 `grep` 解析
- [ ] Agent Memory 模板完全删除
- [ ] Phase A 新增读取 source_materials 步骤

---

## M3 · Student 提示词适配

**难度：** ⭐⭐ 中 | **人力：** 1 人 | **依赖：** M2 完成

### 目标

优化 `evidence-based-student.md` 的提示词，使其更好地遵循 Examiner 输出的结构化指令，同时删除 Agent Memory 模板。模型保持在线 Haiku 不变。

### 具体改动

#### 3.1 删除 Agent Memory 模板

删除 `# Persistent Agent Memory` 及其后全部内容（行 146-280，~135 行）。

#### 3.2 增强格式约束

在 `## 3. Mandatory Response Format` 开头增加：

```
You receive [Student_Instructions] from the Examiner. The format
requirements and question-specific constraints therein OVERRIDE
any defaults below. If Examiner instructions conflict with this
document, the Examiner's instructions take precedence.

You MUST follow the exact answer structure specified in
[Student_Instructions]. Do not deviate. Do not add sections.
Do not omit requested fields.
```

#### 3.3 增加自我检查清单

在 `## 5. Self-Verification Step` 中增加小模型易犯错误的专项检查：

```
6. Did I invent any concept not found in the cited evidence? Delete it.
7. Did I skip the [! Logic Gap] declaration and try to guess? Fix it.
8. Did I use Chinese where the draft uses English notation (or vice versa)?
   Match the draft's language exactly.
9. Did I copy the exact formula from the draft, including subscripts/superscripts?
   A single typo breaks the audit.
```

### 产出物

| 文件 | 说明 |
|------|------|
| `.claude/agents/evidence-based-student.md` | 重写后的 Student |

### 验收标准

- [ ] 文件总行数从 279 降到 ~160
- [ ] Student 遵循 Examiner 的 `[Student_Instructions]`
- [ ] Agent Memory 模板完全删除
- [ ] 不自创概念：知识边界测试通过率 ≥ 95%

---

## M4 · Architect Markdown 输出 + 提示词适配

**难度：** ⭐⭐⭐ 中 | **人力：** 1 人 | **依赖：** M2 完成

### 目标

优化 `content-architect.md` 的提示词，使其遵循 Examiner 输出的 Markdown 创作指令，删除 Agent Memory 模板和冗余协议。模型保持在线 Haiku 不变。输出格式保持 **Markdown + LaTeX**（Haiku 生成 Markdown 的可靠性远高于 HTML），交互组件由后处理管道注入。

### 当前问题

- Agent Memory 模板 ~135 行（行 181-315）
- Ralph-Loop Pre-Write Protocol 对 Haiku 过于复杂
- 原 Mandatory Output Template 需更新以适配 Examiner 的 `[Architect_Instructions]`

### 具体改动

#### 4.1 删除 Agent Memory 模板

删除 `# Persistent Agent Memory` 及其后全部内容（行 181-315）。

#### 4.2 删除 Ralph-Loop Pre-Write Protocol

删除行 45-67（`## Ralph-Loop Pre-Write Protocol`），改由 Examiner 的 `## [Architect_Instructions]` 驱动。

#### 4.3 更新 Mandatory Output Template

替换为基于 Markdown 的输出模板，遵循 Examiner `[Architect_Instructions]` 中的结构要求：

```markdown
## Mandatory Output Template

You receive [Architect_Instructions] from the Examiner. The structure
requirements, content scope, and citation constraints therein OVERRIDE
any defaults below.

### Structure
1. **File Header** (YAML frontmatter)
   ---
   chapter: <chapter-name>
   file_seq: NN
   difficulty: basic|advanced|comprehensive
   confusion_points: [...]
   ---

2. **Learning Objectives & Prerequisites** (## 学习目标与先修前置)
   - 学习目标：bullet list
   - 先修知识：[概念名](filename.md#section) 格式引用

3. **Core Content** (## 核心内容)
   - 定义、定理、公式（LaTeX: $...$, $$...$$）
   - 长推导用 `> [!details]- 完整推导` 折叠

4. **Examples** (## 例题)
   - 题干 + `> [!details]- 查看解答` 折叠解答

5. **Common Mistakes** (## 常见误区)
   - Markdown table: | 错误理解 | 正确理解 |

6. **Self-Check** (## 自测题)
   - 选择题 + 答案 + 解析（不依赖 JS）

### Size Check
If estimated output exceeds the limit in [Architect_Instructions],
output EXAM_TOO_BROAD followed by the bloating defects. Do NOT write.
```

#### 4.4 HTML 后处理管道（可选）

Markdown → HTML 转换由确定性工具完成，不依赖 LLM：

```
drafts/<chapter>/NN.xxx.md
    │
    ▼  pandoc + 自定义 Lua filter
    ├── 折叠标记 → <details><summary>
    ├── 自测题 → <div class="tree-selfcheck"> + JS 反馈
    ├── LaTeX → KaTeX 渲染
    └── 引用链接 → <cite data-ref="...">
    │
    ▼
finished_outputs/<chapter>/NN.xxx.html
```

此管道为**可选**优化，M4 核心交付物是 Markdown 输出。HTML 后处理可在流水线稳定后实施。

### 产出物

| 文件 | 说明 |
|------|------|
| `.claude/agents/content-architect.md` | 重写后的 Architect |
| `templates/md-to-html.lua` | （可选）Pandoc Lua filter，Markdown→HTML 转换 |

### 验收标准

- [ ] Architect 输出为合法 Markdown + LaTeX
- [ ] 遵循 Examiner 的 `[Architect_Instructions]` 结构要求
- [ ] 引用使用 `[概念名](filename.md#section)` 格式
- [ ] 文件总行数从 314 降到 ~180
- [ ] Agent Memory 模板 + Ralph-Loop Protocol 完全删除
- [ ] （可选）HTML 后处理管道输出可在浏览器正确渲染

---

## M5 · RAG 知识索引 + Prompt Cache + 可观测性 + 容错

**难度：** ⭐⭐⭐⭐ 高 | **人力：** 1-2 人 | **依赖：** M2-M4 基本稳定

### 目标

构建基于 Cloudflare Vectorize 的 RAG 知识索引，使子智能体无需在上下文中保留全部前序文件全文，改为按需语义检索。借鉴 NotebookLM 的 grounding + auto-briefing 技术，系统性降低 token 消耗。同时引入 Prompt Caching、可观测性（迭代上限 + 结构化日志）、API 容错机制，确保流水线稳定运行。

### 核心思路：语义检索替代全文注入

| 当前痛点 | RAG 后 |
|----------|--------|
| Student 预读协议：按编号顺序读完所有前序文件全文 | → 语义检索：根据题目检索相关段落，只读命中 chunk |
| Examiner 保留上下文：每轮重读全部前序文件 | → 检索知识边界 + 章节账本摘要，仅在验证引用时回读原文 |
| Architect 保留上下文：记住上一轮草稿+缺陷 | → Bottleneck Report 本身就是结构化指令，无需全文记忆 |
| 总场控传完整文件路径 | → 传检索查询 + 章节账本，agent 按需 pull |

### 架构

```
┌─────────────────────────────────────────────────────────────┐
│                 Cloudflare Vectorize 索引                     │
│                                                              │
│  finished_outputs/<chapter>/NN.xxx.md                        │
│       │                                                      │
│       ▼  chunk + embed（写入时自动索引）                        │
│  ┌──────────────────────────────────────────┐                │
│  │  Vectorize Index: tree-knowledge         │                │
│  │  ┌────────────────────────────────────┐  │                │
│  │  │ chunk_id → embedding → metadata    │  │                │
│  │  │ metadata: {                        │  │                │
│  │  │   chapter, file_seq, section_id,   │  │                │
│  │  │   concepts[], formulas[],          │  │                │
│  │  │   chunk_type: def|proof|example    │  │                │
│  │  │ }                                  │  │                │
│  │  └────────────────────────────────────┘  │                │
│  └──────────────────────────────────────────┘                │
│       │                                                      │
│       ▼  query（检索时）                                       │
│  Worker: tree-rag-query                                      │
│  input: { query_text, top_k, filters }                       │
│  output: [ { chunk, score, metadata } ]                      │
└─────────────────────────────────────────────────────────────┘
```

### 5.1 知识索引构建

#### Chunking 策略

教材文件的 chunk 不能随意切分，需保持语义完整性：

| chunk_type | 切分规则 | 最大长度 |
|------------|----------|----------|
| `def` | 定义/定理：从 `##` 标题到下一段 `##` 标题 | 500 tokens |
| `proof` | 推导/证明：`> [!details]` 折叠块整体 | 800 tokens |
| `example` | 例题+解答：`## 例题` 下的单个例题 | 600 tokens |
| `narrative` | 叙述段落：按段落切分 | 300 tokens |

每个 chunk 的 metadata 包含：
- `chapter`: 章节名
- `file_seq`: 文件序号（如 "03"）
- `section_id`: Markdown 标题锚点（如 "核心内容"）
- `concepts[]`: 该 chunk 涉及的核心概念（由 LLM 提取）
- `formulas[]`: 该 chunk 包含的公式（LaTeX 文本）
- `chunk_type`: def / proof / example / narrative

#### Embedding 模型

使用 LM Studio 本地托管的 embedding 模型：
- `nomic-embed-text-v1.5`（768 维，84MB，已部署）——英文为主，中文基本可用
- 未来可切换到 `bge-m3`（1024 维，多语言）等中文优化模型，LM Studio 支持从 HuggingFace 导入 GGUF 格式

本地 embedding 通过 LM Studio OpenAI 兼容 API（`http://localhost:1234/v1/embeddings`）调用，零费用。

#### 索引写入时机

- **finished_outputs 入库时**：文件 PASS 移入 `finished_outputs/` 时，自动 chunk → embed → upsert 到 Vectorize
- **草稿实时索引**：Step 4 写入/更新草稿后，立即 delete 该文件旧 chunks → re-chunk → upsert 新 chunks。这保证 Step 2 下一轮 Student 检索到的是最新草稿内容
- **草稿索引隔离**：chunk metadata 中标记 `is_draft: true`，查询时可选择是否包含草稿 chunks（Student 需要，Examiner 审计时可选排除）

### 5.2 RAG Query Worker

部署为 Cloudflare Worker：`tree-rag-query`

```typescript
// Worker: tree-rag-query
interface QueryRequest {
  query: string;           // 语义查询文本
  top_k?: number;          // 返回 top K 结果，默认 5
  filters?: {              // 元数据过滤
    chapter?: string;      // 限定章节
    file_seq_gte?: string; // 文件序号 ≥（用于"只看前序文件"）
    chunk_type?: string;   // 限定 chunk 类型
    concepts?: string[];   // 包含特定概念
  };
}

interface QueryResult {
  chunk_id: string;
  text: string;            // chunk 原文
  score: number;           // 相似度分数
  metadata: ChunkMetadata;
}
```

#### 查询模式

| 调用者 | 典型查询 | filters |
|--------|----------|---------|
| Student | 题目中的关键概念/公式 | `file_seq_gte` 限定前序文件 |
| Examiner | "该章节已覆盖哪些概念" | `chapter` 限定当前章 |
| Examiner (审计) | 学生引用的具体段落 | `concepts` 精确匹配 |
| Architect | Bottleneck Report 中的缺陷项 | `chunk_type: "def"` 优先定义 |

### 5.3 NotebookLM 式 Auto-Briefing

NotebookLM 的核心能力之一是为查询自动生成"概览文档"（briefing），让 agent 不需要逐条读 chunk 就能理解全局。

#### 章节账本（Chapter Ledger）

每当文件入库时，自动生成/更新结构化摘要：

```jsonl
// pipeline-temp/<chapter>/chapter-ledger.jsonl
{"file":"01","concepts":["质点","参考系","位置矢量"],"formulas":["\\mathbf{r}=x\\hat{i}+y\\hat{j}+z\\hat{k}"],"methods":["矢量分解"],"chunk_count":12}
{"file":"02","concepts":["位移","速度","加速度"],"formulas":["\\mathbf{v}=d\\mathbf{r}/dt","\\mathbf{a}=d\\mathbf{v}/dt"],"methods":["导数定义"],"chunk_count":15}
```

总场控传账本而非全文路径。Agent 只在需要具体内容时才查询 RAG。

#### Auto-Briefing 生成

当 agent 需要了解某章节的知识全景时，Worker 生成 briefing：

```typescript
// Worker: tree-rag-briefing
interface BriefingRequest {
  chapter: string;
  focus_concepts?: string[];  // 聚焦概念（来自题目/缺陷报告）
}

// 输出：结构化概览（非全文）
interface Briefing {
  chapter: string;
  total_files: number;
  concept_map: { concept: string; defined_in: string; used_in: string[] }[];
  formula_index: { formula: string; context: string; source: string }[];
  knowledge_boundaries: string[];  // 已覆盖 vs 未覆盖的边界
}
```

这替代了"让 agent 读完所有前序文件"——briefing 是压缩的知识地图，agent 按需从 RAG 拉取细节。

### 5.4 上下文传递协议重构

| 步骤 | 当前 | RAG 后 |
|------|------|--------|
| Step 1 (Examiner 命题) | 传全部前序文件路径 → Examiner 读全文 | 传章节账本 + briefing → Examiner 仅对具体知识点 RAG 查询 |
| Step 2 (Student 盲测) | 传全部前序文件路径 + 草稿路径 → Student 预读全文 | 传题目 → Student 对题目关键概念 RAG 检索，只读命中 chunk |
| Step 3 (Examiner 审计) | 传全部前序文件路径 + 草稿 + 答卷 | 传答卷 + 草稿 + 上一轮 Report → Examiner 仅验证引用时 RAG 回查 |
| Step 4 (Architect 重构) | 传全部前序文件路径 + Bottleneck Report + 草稿 | 传 Bottleneck Report + 草稿 → Architect 对缺陷项 RAG 查询相关定义/例题 |

**关键变化**：Examiner 和 Architect 不再需要"上下文保留"——因为 RAG 索引是持久化的外部记忆，每次查询都能获取完整信息。这消除了当前协议中"同一文件循环期间保留上下文"的复杂约束。

### 5.5 Grounding 机制

借鉴 NotebookLM 的 grounding：每条输出必须可溯源到索引中的具体 chunk。

- **Student 答卷**：每条 Evidence 标注 `chunk_id`（替代当前的文件名+段落引用）
- **Architect 草稿**：每个引用链接包含 chunk 来源：`[概念名](filename.md#section)` （chunk_id 在 metadata 中可查）
- **Examiner 审计**：验证 Student 引用的 `chunk_id` 在索引中存在且文本匹配

### 5.6 CLAUDE.md 协议精简

- 环境约束移到 `.claude/settings.json` 的 `instructions` 字段
- Step 0-4 描述改为 RAG 查询协议（传账本 + 查询，而非传文件路径）
- 删除"上下文保留"相关描述（RAG 替代了该需求）
- 预估从 109 行降到 ~60 行

### 5.7 Prompt Caching

DeepSeek API 支持 prefix caching（系统提示 + 消息前缀自动缓存，缓存命中时输入 token 按折扣计费）。

**适用场景**：Examiner 和 Architect 的系统提示 + 章节账本在多轮迭代中几乎不变，天然适合缓存。

**配置方式**：DeepSeek V4 自动启用 prefix caching，无需额外标记。相同前缀的连续请求自动命中缓存。

| Agent | 可缓存部分 | 预估节省 |
|-------|-----------|---------|
| Examiner | 系统提示 + 章节账本 + source_materials 摘要 | ~40% 输入 token |
| Student | 系统提示 + RAG 检索结果（同一份试卷多轮重测时） | ~30% 输入 token |
| Architect | 系统提示 + 章节账本 + Bottleneck Report | ~35% 输入 token |

### 5.8 可观测性与迭代上限

#### 结构化日志

每步写日志到 `pipeline-temp/trace.jsonl`：

```jsonl
{"ts":"2026-05-27T14:30:00Z","step":"S1","chapter":"01-力学","file_seq":"03","agent":"examiner","action":"compose_exam","duration_ms":12000,"route":null}
{"ts":"2026-05-27T14:30:12Z","step":"S2","chapter":"01-力学","file_seq":"03","agent":"student","action":"blind_test","duration_ms":8500,"route":null}
{"ts":"2026-05-27T14:30:20Z","step":"S3","chapter":"01-力学","file_seq":"03","agent":"examiner","action":"audit","duration_ms":15000,"route":"FAIL_KNOWLEDGE_GAP","iteration":1}
{"ts":"2026-05-27T14:31:00Z","step":"S4","chapter":"01-力学","file_seq":"03","agent":"architect","action":"optimize_draft","duration_ms":20000,"route":null}
{"ts":"2026-05-27T14:31:30Z","step":"S3","chapter":"01-力学","file_seq":"03","agent":"examiner","action":"audit","duration_ms":14000,"route":"PASS","iteration":2}
```

#### 迭代上限

同一文件的 Step 2→3→4→2 循环超过 **5 轮**未 PASS 时，流水线自动暂停并输出报警：

```
⚠ ITERATION_LIMIT: 01-力学/03.运动学两类问题.md 已循环 5 轮未 PASS
  最近 5 轮 route: FAIL → FAIL → FAIL → FAIL → FAIL
  请人工检查 Bottleneck Report 和草稿
```

#### RAG Worker 监控

使用 Cloudflare Workers Observability 监控 `tree-rag-query` Worker：
- 延迟 p99 < 500ms
- 错误率 < 1%
- 索引命中率（query 返回结果数 / 总 query 数）

### 5.9 API 容错

全在线模型 = 全程依赖 API，需容错机制：

#### 重试策略

每步 API 调用加 retry：
- 指数退避：1s → 2s → 4s
- 最多 3 次
- 仅对可重试错误（429 限流、500 服务端错误、网络超时）重试
- 不可重试错误（400 参数错误、401 认证失败）直接报错

#### 输出格式校验

Examiner 输出必须包含 `ROUTE:` 行和 `[Blind_Exam]` 段。缺失时：
- 不进 Step 2
- 记录日志
- 重试 Step 1（最多 2 次，之后暂停报错）

Student 输出必须包含 `[Evidence]` 段。缺失时：
- 视为答卷格式错误
- 记录日志
- 重试 Step 2（最多 2 次）

#### 降级策略

DeepSeek V4 Pro 连续 3 次调用失败时，降级到 DeepSeek V4 Flash 做 Examiner（审计质量下降但流水线不中断）。降级状态持续 10 分钟后自动恢复 Pro。

### 产出物

| 文件 | 说明 |
|------|------|
| `workers/tree-rag-query/` | RAG 查询 Worker（Vectorize 绑定） |
| `workers/tree-rag-briefing/` | Auto-briefing 生成 Worker |
| `workers/tree-rag-index/` | 索引写入 Worker（chunk → embed → upsert，含草稿 re-index） |
| `rag/chunker.py` | 教材语义 chunking 脚本 |
| `rag/schema.ts` | Chunk metadata 类型定义 |
| `pipeline-temp/<chapter>/chapter-ledger.jsonl` | 结构化章节账本 |
| `pipeline-temp/trace.jsonl` | 流水线结构化日志 |
| `CLAUDE.md` | 精简后的总场控协议（RAG 查询协议 + 迭代上限 + 容错） |
| `wrangler.jsonc` | Workers + Vectorize 绑定配置 |

### 验收标准

- [ ] Vectorize 索引创建成功，embedding 维度与模型匹配
- [ ] chunking 按语义边界切分，无跨定义/跨证明断裂
- [ ] 草稿写入后实时 re-index，Student 可检索到最新草稿 chunks
- [ ] 草稿 chunks 标记 `is_draft: true`，查询时可过滤
- [ ] RAG query Worker 部署成功，top-5 检索延迟 < 500ms
- [ ] briefing Worker 生成结构化概览，覆盖全部已入库文件
- [ ] Student 通过 RAG 检索作答，不再需要预读全文
- [ ] Examiner 审计引用通过 chunk_id grounding 验证
- [ ] Prompt Caching 配置生效，缓存命中率 ≥ 60%（多轮迭代中）
- [ ] 迭代上限 5 轮触发暂停并输出报警
- [ ] trace.jsonl 每步写入，可回溯流水线执行历史
- [ ] API 重试：429/500 错误自动重试最多 3 次
- [ ] 输出格式校验：缺失 ROUTE/Evidence 时自动重试
- [ ] DeepSeek V4 Pro 降级到 Flash 机制可用
- [ ] CLAUDE.md 从 109 行降到 ~60 行

---

## M6 · 独立平台开发

**难度：** ⭐⭐⭐⭐⭐ 最高 | **人力：** 1-2 人 | **依赖：** M1-M5 全部完成

### 目标

将 T.R.E.E. 从 Claude Code 宿主中解耦，开发独立运行的编排引擎。M1-M5 完成后，所有 agent 提示词、RAG 基础设施、容错机制均已就绪——M6 只需将这些能力移植到自研编排器中，直接调用 DeepSeek API。

### 当前依赖 Claude Code 的部分

| 能力 | 当前实现 | 独立平台需替代 |
|------|---------|---------------|
| 编排逻辑 | CLAUDE.md（系统提示驱动 Claude Code 执行 Step 0-4） | Python/TS 编排器，读取 pipeline-state.json 驱动循环 |
| Agent 调度 | `.claude/agents/*.md`（Claude Code Agent 子进程） | 直接调 DeepSeek API，system prompt 从 .md 文件加载 |
| 模型调用 | Claude Code 内置（Opus/Haiku 代称映射到 DeepSeek） | 直接调 DeepSeek V4 Pro/Flash API |
| 文件操作 | Claude Code 的 Read/Write/Edit 工具 | Python pathlib / Node fs |
| RAG 查询 | Claude Code 内调 Cloudflare Worker | HTTP 请求调 tree-rag-query Worker |
| Git 提交 | Claude Code 内调 git | subprocess / isomorphic-git |
| 可观测性 | trace.jsonl（Claude Code 写入） | 编排器自身写入 |
| Prompt Cache | Claude Code 自动管理 | DeepSeek API prefix caching（自动） |

### 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    tree-engine（编排引擎）                        │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  orchestrator │  │  deepseek   │  │  rag                    │ │
│  │  Step 0→1→2→3│  │  client     │  │  client → Vectorize     │ │
│  │  →4 loop     │  │  Pro/Flash  │  │  → LM Studio embed      │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────────┘ │
│         │                │                     │                │
│  ┌──────┴────────────────┴─────────────────────┴──────────────┐ │
│  │                    pipeline state                           │ │
│  │  pipeline-state.json · trace.jsonl · chapter-ledger.jsonl  │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │  agents/      │  │  io/         │  │  observability/       │ │
│  │  examiner.py  │  │  file_ops    │  │  logger · limiter     │ │
│  │  student.py   │  │  git_ops     │  │  retry · health_check │ │
│  │  architect.py │  │              │  │                       │ │
│  └──────────────┘  └──────────────┘  └───────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 技术选型

| 组件 | 选型 | 原因 |
|------|------|------|
| 语言 | **Python 3.12+** | 与 M1 ingest pipeline 同语言；AI 生态最成熟；asyncio 足合异步 API 调用 |
| LLM 调用 | **openai SDK**（DeepSeek 兼容 OpenAI API） | DeepSeek V4 Pro/Flash 均兼容 OpenAI chat completions API，零适配成本 |
| RAG 客户端 | **httpx** | 异步 HTTP 调 Cloudflare Workers |
| 本地 Embedding | 复用 `rag/embed.py` | 已实现，LM Studio OpenAI 兼容 API |
| 状态管理 | **pydantic** | pipeline-state.json 的类型安全读写 |
| 日志 | **structlog** | JSON 结构化日志 → trace.jsonl |
| CLI | **typer** | `tree run`、`tree status`、`tree resume` 等命令 |

### 核心模块

#### 6.1 deepseek/client.py

```python
from openai import AsyncOpenAI

class DeepSeekClient:
    def __init__(self):
        self.pro = AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com/v1",
        )
        self.flash = AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com/v1",
        )

    async def call_examiner(self, system_prompt: str, user_prompt: str) -> str:
        resp = await self.pro.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content

    async def call_student(self, system_prompt: str, user_prompt: str) -> str:
        resp = await self.flash.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content

    async def call_architect(self, system_prompt: str, user_prompt: str) -> str:
        # Same as student (both use Flash)
        return await self.call_student(system_prompt, user_prompt)
```

#### 6.2 agents/loader.py

从 `.claude/agents/*.md` 加载 system prompt（跳过 YAML frontmatter）：

```python
def load_agent_prompt(agent_name: str) -> str:
    """Load agent system prompt from .claude/agents/<name>.md, stripping frontmatter."""
    path = Path(f".claude/agents/{agent_name}.md")
    content = path.read_text()
    # Strip YAML frontmatter (--- ... ---)
    if content.startswith("---"):
        _, content = content.split("---", 2)[1:]
    return content.strip()
```

#### 6.3 orchestrator/engine.py

核心编排循环，替代 CLAUDE.md 的总场控逻辑：

```python
class TreeEngine:
    async def run(self):
        """Main loop: find in_progress chapter, run Step 0→1→2→3→4 until chapter complete."""
        while True:
            state = load_pipeline_state()
            chapter = state.find_in_progress()
            if not chapter:
                break
            await self.process_chapter(chapter)

    async def process_chapter(self, chapter: ChapterState):
        while True:
            # Step 1: Examiner composes exam
            exam_result = await self.step1_examiner_compose(chapter)
            if exam_result.is_chapter_complete:
                chapter.status = "completed"
                break

            # Step 2: Student blind test
            answer = await self.step2_student_test(chapter, exam_result)

            # Step 3: Examiner audits
            audit = await self.step3_examiner_audit(chapter, exam_result, answer)

            if audit.route == "PASS":
                self.handle_pass(chapter, audit)
                break  # Next knowledge point
            else:
                # Step 4: Architect creates/optimizes draft
                await self.step4_architect_refactor(chapter, audit)
                # Loop back to Step 2 (same exam)
```

#### 6.4 CLI 入口

```bash
# 启动流水线
tree run

# 从中断恢复
tree resume

# 查看状态
tree status

# 单步调试（运行一个 Step）
tree step --chapter 01-力学 --step 1

# RAG 操作
tree rag index --chapter 01-力学
tree rag query "质点定义"
```

### 与 Claude Code 版本的兼容策略

M6 开发期间，两套系统并存：

| 阶段 | 编排器 | Agent 提示词来源 | 模型 |
|------|--------|-----------------|------|
| 当前 | Claude Code（CLAUDE.md） | `.claude/agents/*.md` | Claude Opus/Haiku（代称→DeepSeek） |
| M6 过渡 | tree-engine | 同一套 `.claude/agents/*.md` | 直接调 DeepSeek API |
| M6 完成 | tree-engine | 独立维护的 prompt 文件 | DeepSeek V4 Pro/Flash |

Agent 提示词在 M6 初期直接复用 `.claude/agents/*.md`，通过 `loader.py` 加载。待 tree-engine 稳定后，提示词可迁移到独立目录（如 `tree/prompts/`），脱离 Claude Code 的 frontmatter 格式。

### 产出物

| 文件 | 说明 |
|------|------|
| `tree/` | 独立编排引擎包 |
| `tree/engine.py` | 主编排循环（Step 0→1→2→3→4） |
| `tree/deepseek/client.py` | DeepSeek V4 Pro/Flash API 客户端 |
| `tree/agents/loader.py` | Agent 提示词加载器 |
| `tree/agents/examiner.py` | Examiner 调用封装 |
| `tree/agents/student.py` | Student 调用封装 |
| `tree/agents/architect.py` | Architect 调用封装 |
| `tree/rag/client.py` | RAG 查询客户端（调 Cloudflare Worker） |
| `tree/observability/logger.py` | 结构化日志 → trace.jsonl |
| `tree/observability/limiter.py` | 迭代上限检测 |
| `tree/observability/retry.py` | API 重试 + 降级 |
| `tree/io/file_ops.py` | 文件读写（drafts/finished_outputs） |
| `tree/io/git_ops.py` | Git 提交 |
| `tree/state/models.py` | pipeline-state.json Pydantic 模型 |
| `tree/cli.py` | CLI 入口（typer） |
| `pyproject.toml` | 项目配置 |

### 验收标准

- [ ] `tree run` 可完整运行一个知识点的 Step 1→2→3→4 循环
- [ ] `tree resume` 可从中断点恢复
- [ ] `tree status` 显示当前章节、文件进度、迭代轮次
- [ ] DeepSeek V4 Pro/Flash API 调用成功，响应格式正确
- [ ] Agent 提示词从 `.claude/agents/*.md` 正确加载
- [ ] RAG 查询通过 HTTP 调 Cloudflare Worker 成功
- [ ] trace.jsonl 每步写入
- [ ] 迭代上限 5 轮触发暂停
- [ ] API 重试 + Pro→Flash 降级机制工作
- [ ] 与 Claude Code 版本产出相同质量的教材文件

---

## 附录 A：Agent Memory 瘦身（跨 M2/M3/M4）

投入产出比最高的单项优化。当前三个 agent 文件末尾各自包含 ~130-135 行的 Persistent Agent Memory 使用说明模板，共计 ~400 行。

**删除内容（每个 agent 文件）：**
- `# Persistent Agent Memory` → 文件末尾
- 包含：Types of memory 定义、What NOT to save、How to save memories、When to access memories、Before recommending from memory、Memory and other forms of persistence、MEMORY.md 空索引

**保留内容：**
- 如果 agent 确实需要记录项目级记忆（如 Architect 记录领域知识模式），迁移到简短的项目级 memory 目录，通过 CLAUDE.md 统一管理。

| 文件 | 删除行数 | 预计节省 |
|------|---------|---------|
| faithfulness-examiner.md | ~135 行（202-336） | ~40% |
| evidence-based-student.md | ~135 行（146-280） | ~48% |
| content-architect.md | ~135 行（181-315） | ~43% |

> **注意**：M5 的 RAG 索引实现后，Agent Memory 的需求进一步降低——RAG 本身就是持久化的外部记忆。

---

## 附录 B：文件变更总览

| 文件 | 模块 | 操作 |
|------|------|------|
| `ingest/` | M1 | **新增** |
| `templates/md-to-html.lua` | M4 | **新增**（可选） |
| `workers/tree-rag-query/` | M5 | **新增** |
| `workers/tree-rag-briefing/` | M5 | **新增** |
| `workers/tree-rag-index/` | M5 | **新增** |
| `rag/chunker.py` | M5 | **新增** |
| `rag/embed.py` | M5 | **新增** |
| `rag/schema.ts` | M5 | **新增** |
| `scripts/setup-embedding.sh` | M5 | **新增** |
| `.claude/agents/faithfulness-examiner.md` | M2 | **重写** |
| `.claude/agents/evidence-based-student.md` | M3 | **重写** |
| `.claude/agents/content-architect.md` | M4 | **重写** |
| `CLAUDE.md` | M5 | **精简** |
| `wrangler.jsonc` | M5 | **新增/修改** |
| `pipeline-temp/<chapter>/chapter-ledger.jsonl` | M5 | **新增** |
| `pipeline-temp/trace.jsonl` | M5 | **新增** |
| `tree/` | M6 | **新增** |
| `pyproject.toml` | M6 | **新增** |

---

## 附录 C：v1.0 → v2.3 变更摘要

| 项目 | v1.0 | v2.3 | 原因 |
|------|------|------|------|
| Student 模型 | Qwen3.6-27B-Q4 本地 | DeepSeek V4 Flash | 放弃本地模型，使用在线 Flash 级 |
| Architect 模型 | Qwen3.6-27B-Q4 本地 | DeepSeek V4 Flash | 放弃本地模型，使用在线 Flash 级 |
| Examiner 模型 | Claude Opus（代称） | DeepSeek V4 Pro | 还原实际模型 |
| M1 OCR 引擎 | PaddleOCR-VL 本地部署 | PaddleOCR-VL v1.5 API 客户端 | Apple Silicon 无 GPU 支持，远程服务可用 GPU 加速 |
| M1 本地依赖 | PaddlePaddle + PaddleOCR + OpenCV | httpx + Pillow + PyMuPDF | 删除 ~2GB 本地 OCR 依赖，改用 HTTP 客户端 |
| M1 结构化整理 | Qwen 本地 | DeepSeek V4 Flash | 放弃本地模型 |
| M4 输出格式 | HTML（LLM 生成） | Markdown + LaTeX（HTML 后处理可选） | Flash 级模型生成 Markdown 可靠性远高于 HTML |
| M5 核心手段 | 章节账本 + 文本精简 | Vectorize RAG + 本地 LM Studio embedding + auto-briefing + grounding + Prompt Cache | RAG 替代全文注入，本地 embedding 零费用 |
| 草稿索引 | 无 | 实时 re-index（Step 4 后同步更新） | Student/Examiner 需检索最新草稿 |
| 可观测性 | 无 | trace.jsonl + 迭代上限 5 轮 + RAG Worker 监控 | 防死循环，可回溯 |
| API 容错 | 无 | 重试 + 格式校验 + Pro→Flash 降级 | 全在线依赖，需容错 |
| 上下文保留 | Examiner/Architect 保留上下文 | RAG 索引 = 持久化外部记忆，无需保留 | 消除"上下文保留"复杂约束 |
| 硬件约束 | 24GB 内存、Ollama 部署 | 无本地模型约束 | 全在线 |
| 运行宿主 | Claude Code | M6: 自研 tree-engine | 解耦 Claude Code，直接调 DeepSeek API |
