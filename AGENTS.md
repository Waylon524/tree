# 核心任务：资料驱动的以考促写教材生成流水线 (T.R.E.E. System)

当前工作目录下包含以下核心运行目录：
- `/raw_materials`: 用户上传的原始资料，可为 PDF、图片、DOCX、Markdown、TXT。
- `/source_materials`: PaddleOCR + Archivist 处理后的结构化 Markdown 中间产物，成功 embedding 后删除。
- `/drafts`: writer 在 Step 4 创建或优化的知识点草稿。
- `/finished_outputs`: 满分通过后入库的最终教材文件。
- `examiner`, `student`, `writer`, `archivist`: 引擎内置 prompt 角色；运行时不依赖 `.claude/agents` 文件夹。

## 核心范式：资料驱动 + 以考促写

流水线不使用预定义知识点清单、章节清单或外部习题库。用户上传资料后，引擎先将原始资料转为结构化 `source_materials/<collection>/*.md`，完成 embedding 后删除中间 Markdown。随后 examiner 基于 RAG 中的 source chunk 自主发现章节、确定下一个知识点并命题；student 只依据前序成品 embedding 与当前草稿作答；examiner 审计答卷；writer 按缺陷报告创建或优化草稿。循环直到所有已入库资料覆盖完毕。

## 资料摄入流程

1. 用户提供原始文件或目录。
2. `tree-run ingest --input <file-or-dir> --collection <collection-name>` 调用 PaddleOCR 处理原始文件。
3. Archivist 将 OCR 原始结果清洗、纠错、重组为教材可用 Markdown。
4. 输出写入 `source_materials/<collection-name>/`。
5. source Markdown 完成 RAG embedding 后自动删除。
6. `tree-run run` 从 RAG 中的 source chunk 发现章节并启动以考促写循环。

## 自动化执行循环

### Step 0: 上下文管理
- 从 `pipeline-state.json` 中找到 `status: in_progress` 的章节，读取其 `chapter_name`、`source_collection` 和 `files_completed`。
- 确定下一个文件序号 = 当前章节 `files_completed` 数量 + 1。
- examiner 和 writer 在同一文件循环内保留上下文连续性。
- student 每轮全新启动，只能从文件系统输入中重新学习。

### Step 1: examiner 基于结构化资料自主命题
- 提供给 examiner：
  - 下一个文件序号。
  - 当前章节 `source_collection` 下的 source RAG chunk 与来源路径。
  - 全部前序已完成文件路径与 finished RAG chunk。
  - 若从 `EXAM_TOO_BROAD` 退回，附加膨胀缺陷项与已暂存知识点名称。
- examiner 任务：
  1. 读取结构化资料，识别尚未覆盖的知识边界。
  2. 确定下一个知识点名称。
  3. 基于结构化资料自创 3 道盲考试题。
  4. 输出 `## [Next_Knowledge_Point]` + `## [Blind_Exam]` + `## [Answer_Key]` + `## [Writer_Instructions]`。
- 若当前章节资料已闭合，输出 `CHAPTER_COMPLETE`。

### Step 2: student 盲测与取证
- student 只获得：
  - 前序已完成文件。
  - 当前知识点草稿（若存在）。
  - examiner 的 `[Blind_Exam]`。
- student 不读取 `source_materials/`，避免直接从源资料绕过草稿。
- 每道题必须按证据、推导、缺口、反馈结构作答。

### Step 3: examiner 双重审计
- examiner 获得：
  - 当前草稿全文（若不存在则注明尚未创建）。
  - 本轮试卷原文。
  - 标准答案。
  - student 答卷。
  - 上一轮 Bottleneck Report（若存在）。
  - 前序已完成文件路径与 finished RAG chunk。
  - 当前章节 source RAG chunk，用于确认 writer 应覆盖的知识范围。
- PASS 条件：答案全对、推理全由草稿或前序成品支持、无知识越界、无逻辑缺口、无知识缺陷。
- FAIL 时输出 Bottleneck Report 并路由到 Step 4。

### Step 4: writer 靶向创建或优化
- writer 获得：
  - 知识点名称与序号。
  - 最新 Bottleneck Report。
  - 上一轮 Bottleneck Report（若存在）。
  - 当前草稿全文（若存在）。
  - 前序已完成文件路径与 finished RAG chunk。
  - examiner 的 `[Writer_Instructions]`。
- writer 创建或优化 `drafts/<chapter>/<NN>.<知识点>.md`。
- 若预计覆盖缺陷会超过规模限制，输出 `EXAM_TOO_BROAD`，总场控回 Step 1 缩小命题范围。
- 正常写入草稿后，强制回 Step 2 使用同一份试卷重测。

## 章节闭合与续章

`CHAPTER_COMPLETE` 只表示当前 source collection 覆盖完毕，不表示整个流水线结束。

章节闭合后：
1. 将当前章节状态改为 `completed`。
2. examiner 扫描 RAG 中的 source collection，与 `pipeline-state.json` 中已完成章节比对。
3. 若存在未覆盖 collection，创建新的 `in_progress` 章节并继续。
4. 若所有结构化资料均覆盖，输出 `PIPELINE_COMPLETE`。

## 状态追踪

`pipeline-state.json` 中每个章节包含：
- `chapter_name`
- `source_collection`
- `status`
- `files_completed`

每次文件 PASS 后追加到当前章节的 `files_completed`。

## 运行模式

流水线连续运行，文件入库后自动进入下一个知识点；章节闭合后自动进入下一章。禁止在中间节点询问是否继续。仅在 `PIPELINE_COMPLETE` 或需要人工决策的阻塞条件下暂停。

## 中断恢复

若会话中断，下次启动时：
1. 读取 `pipeline-state.json`。
2. 找到 `status: in_progress` 的章节。
3. 从该章节 `files_completed` 数量 + 1 的文件序号继续。
4. 若无进行中章节，则扫描 `source_materials/` 判断是否还有未覆盖 collection。
