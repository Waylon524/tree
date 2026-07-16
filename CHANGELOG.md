# Changelog

本文件记录项目每次已落地变更，按时间倒序维护。未来计划请查看 [PLAN.md](PLAN.md)。

## Unreleased - 2026-07-16

### Added

- 增加项目级有界轮转 LLM operation JSONL，记录安全的成功、重试、输出截断、provider 能力降级和失败摘要；`tre logs` 可发现该日志，GUI 提供最近 operation 诊断接口。
- 为 Archivist 与 Dagger 的全部 AI JSON 边界增加严格 Pydantic schema 和唯一 JSON 对象提取；字段类型、必填内容、未知字段与跨对象一致性错误现在进入受控 repair，并在耗尽后失败关闭。
- 为 Examiner 生成的 Writer Instructions 增加严格结构模型，校验覆盖节点、教学范围、必需概念、公式、推导、禁止越界项和先修缺口后才允许传给 Writer。
- 增加 `auto`、`deepseek`、`openai`、`generic` provider profile，以及全局/角色级 context window、输出预留和安全余量；未知兼容端点只发送标准字段，明确的不支持参数会在当前 provider/model 下做一次可观测降级并缓存。
- 增加请求前 token 硬门槛、RAG/repair 可选上下文裁剪和 Dagger 覆盖输入递归分批；预算日志只记录数量，不记录提示词、密钥或学生答案。
- 增加覆盖全部 AI 调用的 operation 规格，按任务配置输出 token、timeout、thinking/reasoning、JSON 模式和重试上限，并记录安全的 usage、耗时、重试、终止原因与降级遥测。

### Changed

- 将全部 AI operation 的 Max Tokens 上限提高到原配置的十六倍：`512→8192`、`2048→32768`、`4096→65536`、`8192→131072`；默认角色级输出上限同步提高到 `131072`，默认 DeepSeek V4 Flash context window 校准为 `1000000`，在保持短修复请求分档的同时降低长输出截断概率。
- LLM 响应现在统一验证 choices、message、content、refusal、tool calls 与 `finish_reason`，仅在完整响应通过契约后记录 provider 成功；异常退出会把流水线、当前阶段和 active 状态统一收敛到失败或停止终态。
- Examiner 要求完整且唯一的五段试卷、路由与 reconciliation 标记，并验证 `Covered_Node_IDs`；Writer 拒绝空教学正文和带项目符号/编号/引用变体的试卷或答案区块。
- Writer 的规则优先级固定为代码硬约束、结构化 Writer Instructions、动态上下文；草稿、Bottleneck、RAG 和反馈统一作为不可信数据传入，已完成先修默认只引用而不重教。
- Dagger 的每个目标节点必须显式返回 `internal_prerequisite_decision=selected|none`，不再把缺失返回静默解释为无前置；建图会验证覆盖、define 来源和自依赖，并对异常高根节点比例执行一次全局复核而不强制补边。
- Dagger 遇到节点构建输出截断时不再原样重试，而是递归拆批；环修复只允许删除当前环上的必要前置边，并冻结非环节点、外部前置和合法多根/并行结构。
- Planner 缓存签名现在包含有效 Archivist/Dagger prompt 哈希、算法/schema 版本和相应语义配置；API key 与原始 prompt 正文不进入签名。

### Fixed

- 修复并行 PDF OCR 用分块序号推算累计页数导致进度提前和倒退的问题；现在逐分块保存单调最大页数后求和，兼容乱序、重复、重试、下载恢复和结果复用事件。
- 修复主动暂停后持久化 `in_progress` 检查点被 CLI 和 GUI 错当成实时 active 的问题；停止现在统一收敛 phase/stage 状态、取消尚未结束的 NodeRun 子任务，同时保留完整检查点供下次续跑。
- 修复可恢复的 pypdf 缺失对象和重复 `/Filter` 警告重复刷屏的问题；页数检查和分块作用域内只输出带文件名与分类计数的摘要，未知警告与完整性错误仍正常报告。
- 修复 Archivist Clean 只按 100000 字符拆分、短行密集课件仍可能触发 4096-token 输出截断的问题；请求现在同时限制为最多 1000 行和 100000 字符，截断时递归二分，最小窗口仍失败则保守保留原文，并通过新版 Planner 签名失效旧清洗缓存。
- 修复含超大嵌入资源的 PDF 在 OCR 分块时触发 pypdf `LimitReachedError` 的问题；只在本地读取期间把声明流上限提升到文件实际字节数，处理后恢复默认 75 MB 阈值，并保留其他解压与图片安全限制。
- 修复某个并发材料失败后，迟到 OCR 回调把采集、筛净和分种重新显示为“进行中”的竞态；失败阶段、全局阶段和结构化错误现在原子写入，旧失败快照也会在 API 展示层归一化。
- 修复生长页在常用桌面窗口中状态徽标逐字断行、长文件名挤压进度列和异常信息重复的问题；进度行改为响应式网格，失败摘要集中到单个错误卡片，并覆盖紧凑窗口布局。
- 修复“重新生长”沿用旧试卷、草稿和迭代历史的问题；Regrow 现在从新试卷开始并清空旧输出状态，普通失败恢复仍保留检查点。
- 修复 Archivist metadata repair 的 system prompt 要求外层 `unit`、解析器却只接受字段对象的契约冲突；标题、defines 和摘要修复现在与严格 schema 完全一致。

## 0.3.7 - 2026-07-15

### Added

- 为进度状态增加 run ID、Planner generation ID、迟到事件隔离和结构化错误记录；错误包含阶段、资源、重试次数、可恢复性与建议动作。
- 增加供应商级共享自适应并发限制器、瞬时错误分类、`Retry-After` 解析和渐进恢复。
- 增加 NodeRun 重复 Bottleneck 停滞检测、审查历史与部分完成状态。
- 增加 `packaging/test_local.sh`，通过当前 Python 解释器模块入口执行测试，避免失效 pytest shebang 指向旧 checkout。
- 为源码与打包 sidecar 增加真实 PDF AES crypto doctor 检查，并在 macOS/Windows 发布流程中执行。

### Changed

- 七阶段进度改为累计项目语义：缓存命中、失败重启和断点续跑继承真实 `done/total`，`0/0` 不再显示为 `100%`。
- 单个 NodeRun 失败不再抹掉已完成结果；精准重试继承试卷、草稿、迭代、Bottleneck 和一次性试卷修复计数。
- 默认并发收敛为 LLM provider `4`、材料 `4`、Archivist 分块 `2`、Dagger prerequisites `3`、NodeRun `3`，并同步后端、桌面设置与配置文件。
- 非空材料被清洗为空时重试一次并明确失败；MTU 20 行、标题和摘要长度改为本地合并或确定性规范化，行覆盖、来源、define 和防虚构门禁保持严格。
- 清洗源码在嵌入后继续保留，支持缺失 MTU 定向补嵌入与验证；高 singleton 比例改为质量告警，Link 环在模型修复耗尽后删除最低置信边。
- 高级设置保存结果现在说明受配置变化影响、下次需要重算的阶段。

### Fixed

- 修复干净 GitHub Actions runner 在 Rust 单元测试编译阶段因尚未生成 Tauri sidecar resource 目录而提前失败的问题；正式平台打包仍构建并严格检查真实 sidecar。
- 修复正式 sidecar 缺少 `cryptography` 导致 AES PDF 在 Gather/OCR 阶段报 `DependencyError` 的问题，并区分密码文件、crypto 缺失和不支持算法。
- 修复重新运行后进度清零、缓存阶段显示伪百分比、旧运行回调覆盖新状态，以及部分失败被整体标成失败的问题。
- 修复同一 Examiner Bottleneck 反复改写正确草稿直至耗尽迭代的问题。
- 修复错误面板重复显示阶段错误、底层异常和退出阶段 traceback，并过滤 `NameError: name 'open' is not defined` 等关闭噪音。
