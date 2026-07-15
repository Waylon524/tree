# Changelog

本文件记录项目每次已落地变更，按时间倒序维护。未来计划请查看 [PLAN.md](PLAN.md)。

## Unreleased

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
