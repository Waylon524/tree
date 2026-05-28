# 核心任务：以考促写的自动化教材生成流水线 (T.R.E.E. System)

当前工作目录下包含以下结构：
- `/drafts`: 存放流水线自动生成的知识点草稿 (.md)。初始为空，由 writer 在 Step 4 创建。
- `/exercises`: 存放配套的习题库与标准答案。
- `/finished_outputs`: 存放最终满分通过的教材。
- `writer`, `student`, `examiner`: 子智能体规则（分别对应旧协议中的 content-architect、evidence-based-student、faithfulness-examiner）。

## 核心范式：以考促写 (Exam-Driven Writing)

无预定义知识点清单，亦无预定义章节清单。流水线从已完成的最后一个文件出发，**考官自主决定下一个知识点并命题，学生考试，根据考试结果生成草稿**，循环至考官判定当前章节知识闭合。章节闭合后，考官扫描 `exercises/` 目录自主发现并命名下一章——章节清单随流水线运行而**动态生长**，而非预先填写。

## 自动化执行循环 (Auto-Loop Protocol)

### Step 0: 上下文管理 — 每次处理新文件前必须执行
- 从 `pipeline-state.json` 中找到 `status: in_progress` 的章节，读取其 `chapter_name` 和 `files_completed` 列表。
- 确定下一个文件序号 = 当前章节 `files_completed` 数量 + 1（如已完成 2 个，则下一个是 03）。
- **考官和建筑师保留上下文**：同一文件循环期间，examiner 和 writer 的每次调用保持上下文连续（考官记住自己命的题和审过的答卷；建筑师记住上一轮的草稿和知识缺陷）。
- **学生每次全新启动**：student 每轮调用均全新启动，不携带之前上下文。预读协议保证它每次从文件系统重新加载全部知识。

### Step 1: 考官自主命题
- 全新加载 `examiner` subagent。
- 提供给考官：
  - 下一个文件序号（如 "03"）
  - **全部前序已完成文件的完整路径**
  - 习题库路径：`exercises/<chapter>-<章节名>.md`
  - **若从 EXAM_TOO_BROAD 退回**：附加膨胀的缺陷项列表 + 已暂存的知识点名称（总场控在首轮 Step 1 后已保存）。若草稿已存在则附加草稿路径。考官需**复用原有知识点名称**，但缩减试卷范围——删除或替换导致膨胀的题型，减少单次迭代的知识覆盖面。
- **考官任务**：
  1. 读取全部前序已完成文件，理解知识边界
  2. 确定知识点名称（首轮自主命名；EXAM_TOO_BROAD 退回时复用总场控传入的名称）
  3. 从习题库找题或自创题目（从 EXAM_TOO_BROAD 退回时，题目数量/覆盖面应显著减少）
  4. 输出 `## [Next_Knowledge_Point]` + `## [Blind_Exam]` + `## [Student_Instructions]` + `## [Answer_Key]` + `## [Architect_Instructions]`
- **终止信号**：若考官输出 `CHAPTER_COMPLETE`，总场控执行章节闭合流程（见下方"章节闭合与续章"）。

### Step 2: 盲测与取证
- **Step 1 成功后总场控暂存**：提取考官输出的 `## [Next_Knowledge_Point]`（知识点名称）、`## [Blind_Exam]`（试卷原文）、`## [Student_Instructions]`（学生答题约束）、`## [Answer_Key]`（标准答案）、`## [Architect_Instructions]`（建筑师写作约束）。五者在整个文件循环期间保持不变。
- 全新加载 `student` subagent。
- 提供学生阅读清单：
  - **全部前序已完成文件**（`finished_outputs/<chapter>/` 下全部文件，按编号排列）
  - **当前知识点草稿**（若 `/drafts` 中已存在；首次循环不存在）
- 提供 `[Blind_Exam]`。
- 学生按预读协议先读全部文件再作答。
- 获取答卷。

### Step 3: 双重审计与决断
- 全新加载 `examiner` subagent。**传入完整上下文使其"延续"**：
  - 当前知识点草稿全文（存在则嵌入，不存在则注明 "尚未创建"）
  - **本轮试卷原文**（考官在 Step 1 自己命的题）
  - 标准答案
  - 学生答卷全文
  - **上一轮 Bottleneck Report**（若存在——帮助考官追踪修复进展）
  - **前序已完成文件的完整路径**（考官需自行读取以验证学生引用）
- 考官审计，生成 Bottleneck Report。
- **判定与路由**：
  - ✅ **PASS**：草稿移入 `/finished_outputs`，更新 `files_completed`，回 Step 1。**若 PASS 时 `/drafts` 中无对应草稿（题目全可凭前序知识解答，无增量内容）**：不产生新文件，不追加 `files_completed`，回 Step 1。考官将自然发现无新知识可考，输出 CHAPTER_COMPLETE。
  - 🔧 **知识缺陷**：进入 Step 4。不重新组卷，下一轮 Step 2 用同一份试卷。

### Step 4: 靶向重构 / 创建
- 全新加载 `writer` subagent。**传入完整上下文使其"延续"**：
  - 知识点名称/序号
  - 最新 Bottleneck Report
  - **上一轮 Bottleneck Report**（若存在——帮助建筑师理解历史缺陷和修复进展）
  - **当前草稿全文**（OPTIMIZE 模式时嵌入，CREATE 模式时注明不存在）
  - 前序已完成文件列表
- **CREATE**（草稿不存在）或 **OPTIMIZE**（草稿存在）。
- 建筑师在落笔前执行规模检查：若覆盖所有知识缺陷预计产出 **>1000 行**，则**拒绝写入**，输出 `EXAM_TOO_BROAD` + 造成膨胀的具体缺陷项。
- **若建筑师输出 `EXAM_TOO_BROAD`**：总场控将膨胀的缺陷项列表传回考官，退回 Step 1 重新命题（缩减试卷范围，删除部分膨胀题型）。草稿文件保持原样不动。
- 正常情况下，建筑师写入草稿并 Git 提交后，**强制退回 Step 2**（同一份试卷）。

### 章节闭合与续章 (Chapter Close & Continuation)

**`CHAPTER_COMPLETE` ≠ 流水线终止。** 它只是当前章节的知识边界闭合。

当 Step 1 考官输出 `CHAPTER_COMPLETE` 后，总场控执行：

1. 将当前章节在 `pipeline-state.json` 中的 `status` 改为 `completed`。
2. **立即重新加载考官**（新一轮 Step 1），传入：
   - `pipeline-state.json` 全文（含所有已完成章节）
   - `exercises/` 目录路径
3. 考官扫描 `exercises/` 中所有习题，与已完成章节覆盖的知识面进行比对，自主判断：
    - **存在未被覆盖的习题** → 考官自主命名新章节，输出新章节的 `## [Next_Knowledge_Point]` + `## [Blind_Exam]` + `## [Student_Instructions]` + `## [Answer_Key]` + `## [Architect_Instructions]`。总场控将新章节追加到 `pipeline-state.json`（`status: in_progress`，`files_completed: []`），流水线继续。
   - **无可考内容** → 考官输出 `PIPELINE_COMPLETE`。总场控输出完成报告，流水线终止。

`pipeline-state.json` 中的章节记录随流水线运行**动态生长**——考官每发现一个新章节即追加一条，不存在预定义的章节清单。

**章节闭合后不暂停**：流水线自动进入下一章的 Step 1，与文件级循环一样无需人工干预。

**系统启动指令**：输出 "T.R.E.E. 系统启动，已加载以考促写协议。"，从 `pipeline-state.json` 中找到第一个 status 为 `in_progress` 的章节，开始 Step 1。

## 状态追踪
使用 `pipeline-state.json` 追踪进度。章节记录随流水线运行**动态追加**，不存在预定义清单：
- 每个章节包含 `chapter_name`、`status`（`in_progress` / `completed`）、`files_completed`（已完成文件列表）
- 每次文件 PASS 后追加到当前章节的 `files_completed`
- 考官发现新章节时，总场控向 `pipeline-state.json` 追加一条新记录

## 运行模式
- 流水线连续运行，文件入库后自动进入下一个知识点的 Step 1，章节闭合后自动进入下一章的 Step 1，**禁止**在任何中间节点询问"是否继续"。仅在 `PIPELINE_COMPLETE`（无可考内容）或遇到需要人工决策的阻塞（如连续 EXAM_TOO_BROAD）时才暂停。

## 环境约束
- 工作目录为当前项目根目录（即 AGENTS.md 所在目录），所有 git 命令直接执行（如 `git status`、`git add`、`git commit`），**禁止**使用 `cd` 前缀。多余的 cd 会触发不必要的权限审核。
- **文件操作必须使用专用工具**：列出目录内容用 `Glob`，读取文件用 `Read`，**禁止**使用 `Get-ChildItem`、`ls`、`cat`、`Get-Content` 等 shell 命令进行文件浏览。这些专用工具不会触发权限或安全提示。
- **PowerShell 禁止花括号+引号组合**：`if (...) { Write-Host "..." }` 或 `2>$null; if (-not $?) { echo "..." }` 等命令会触发 "brace with quote character" 安全拦截。若必须用 PowerShell，使用 `-ErrorAction SilentlyContinue` 替代错误处理花括号块。

## 中断恢复
若会话被中断，下次启动时：
1. 读取 `pipeline-state.json`
2. 找到 status 为 `in_progress` 的章节（若全部 `completed`，重新加载考官扫描 exercises 判断是否有续章）
3. 从该章节 `files_completed` 数量 + 1 的文件序号继续
