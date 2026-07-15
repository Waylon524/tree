# Changelog

本文件记录项目每次已落地变更，按时间倒序维护。未来计划请查看 [PLAN.md](PLAN.md)。

## Unreleased

### Added

- 为知识生成流水线增加按材料增量缓存、generation 一致性校验、输出事务恢复和更完整的失败诊断。
- 增加共享的 Button、Card、Field、Menu、Message、Toggle、确认面板、格式化与导出前端组件。
- 增加 release doctor、Python 依赖约束、mypy 回归基线和本机 macOS 发布脚本。

### Changed

- 强化 OCR、Archivist、Dagger、RAG 与 NodeRun 的并发、重试、恢复和质量门禁，并补齐对应测试。
- 将桌面页面迁移到共享 UI 组件，统一导出流程、操作状态和 Grow 运行前置条件。
- 将 CI 扩展为三平台 Python 验证、前端构建测试和 Rust shell 检查；正式 Release workflow 聚焦 Windows，macOS 保持本机签名与公证。
- 建立 README、PLAN 和 CHANGELOG 的文档职责与同步维护规则。
- 根据当前源码、构建脚本和 `v0.3.6` 发布历史更新项目说明，并记录可执行的桌面打包与发布流程。
- 将旧产品化和打包阶段清单收敛为历史决策记录，排除本机助手状态和 Office 临时文件，并纳入项目汇报资产。
