# TREE Productization Decision Record

> 本文件保留 2026-06-18 桌面产品化工作的关键决策与落地结果，不再作为待办清单。
> 当前能力与用法以 [README.md](README.md) 为准；已确认但尚未完成的工作只记录在
> [PLAN.md](PLAN.md)；实现历史记录在 [CHANGELOG.md](CHANGELOG.md)。

## 原始目标

将 TREE 从以工作目录和 CLI 为中心的开发工具，改造成以项目为中心的桌面学习 App：
用户导入材料、运行生成流水线、查看知识图谱、阅读生成文件，并在不接触内部目录结构的
情况下导出结果或迁移整个项目。

## 已确认的产品决策

- 用户面对的是“项目”，不是 `materials/`、`outputs/` 和 `.tree/runtime/` 等内部目录。
- 每个项目拥有独立的材料、生成结果、知识图谱、RAG、运行状态和学习进度。
- LLM、OCR、embedding 运行环境和默认模型属于用户级全局设置。
- 项目显示名称可以修改，但稳定的项目 ID 和存储路径不随之移动。
- 导入材料时复制到项目内部；生成结果默认保存在项目内，按用户选择导出。
- 项目迁移包包含继续生成和学习所需的数据，但不包含 API 密钥和易失服务状态。
- Tauri 壳负责项目注册表、项目切换和 Python sidecar 生命周期；Python engine 每次只处理
  一个项目根目录。

## 当前落地结果

- 受管理项目保存在 `~/.tree/projects/proj_<uuid>/`，注册表位于
  `~/.tree/projects/index.json`。
- Orchard 已支持新建、导入已有 workspace、从 Parent Tree 导入、重命名、复制迁移包、
  迁出和删除项目。
- React App 已提供 Tend、Grow、Harvest、Reader 和 Fruits 页面，覆盖材料、设置、生成、
  知识图谱、阅读、反馈修订与导出。
- Rust 壳通过本机端口和每次启动生成的 token 连接 Python sidecar；浏览器/CLI GUI 仍作为
  调试与高级入口保留。
- Parent Tree zip 会保留项目材料、输出与运行状态，排除 `.env`、`.tree/config.env`、日志、
  PID 和服务临时文件，并在导入时防止不安全归档路径。

## 当前文档入口

- 使用方式与当前能力：[README.md](README.md)
- 唯一有效的未来计划：[PLAN.md](PLAN.md)
- 已完成变更：[CHANGELOG.md](CHANGELOG.md)
- 维护与发布规则：[AGENTS.md](AGENTS.md)
