# Repository Guidance

## Documentation Stewardship

根目录的 `README.md`、`PLAN.md` 和 `CHANGELOG.md` 是项目文档的固定入口，职责必须分开：

- `README.md`：只描述项目**当前**情况，包括当前能力、使用方式、运行要求、当前源码或发布状态。不要把变更历史、已完成任务、临时排查记录或未经确认的未来设想堆入 README。
- `PLAN.md`：只记录与用户讨论后**已确认且尚未完成**的未来计划。每次计划讨论得出确认结论后立即更新；计划完成、取消或被替代后，及时删除或改写，不能把它维护成完成事项或讨论纪要的累积列表。
- `CHANGELOG.md`：按时间倒序记录每次已经落地的项目变更。它是历史记录，可以保留旧条目；不要把未来计划写入其中。

`PRODUCTIZATION_PLAN.md` 和 `PACKAGING_PLAN.md` 只保留已经落地的历史决策与架构边界，
不得再维护进行中的阶段清单或待办。任何仍然有效且已经确认的未完成事项必须移入
`PLAN.md`；未确认设想直接删除，不在历史决策记录中长期保留。

## Required Update Flow

每次代码变动完成后，必须在同一改动中同步审阅并更新这三份文档：

1. 更新 `README.md`，使项目当前状态、能力、用法和版本信息准确，并清理过时内容。
2. 更新 `PLAN.md`，保留仍然有效的已确认计划，移除已完成、取消或失效的事项。
3. 更新 `CHANGELOG.md`，记录本次实际变更及其影响。

每次与用户讨论项目未来方向并形成确认结论后，即使尚未修改代码，也必须立即更新 `PLAN.md`。更新 README 或 PLAN 时优先替换和删除过期内容，保持简短、准确、面向当前状态；不要为保留历史而持续追加。没有实质内容可改时，完成同步审阅即可，不要添加无意义的日期或占位文字。

## Packaging and Release

TREE 的发布产物是 Tauri 桌面安装包：React 前端由 Tauri 打包，Python engine 用 PyInstaller 打为 `tre-engine` sidecar，并作为 Tauri resource 一同交付。sidecar 必须在目标系统本机构建，不能跨平台复用。

### Version and release gate

1. 先在干净的、准备发布的源码提交上完成验证。正式发布禁止使用 `--allow-dirty`。
2. 将同一个版本号同步到七个版本字段（分布在六个文件中）：`pyproject.toml`、`desktop/package.json`、`desktop/package-lock.json`（顶层与 packages root 两个字段）、`desktop/src-tauri/Cargo.toml`、`desktop/src-tauri/Cargo.lock` 和 `desktop/src-tauri/tauri.conf.json`。
3. 运行 `python3 packaging/release_doctor.py --tag v<version>`。它会校验版本一致、工作区干净、所需 lock/约束文件齐全，以及 tag 与版本匹配。
4. 只从已验证且应纳入 `main` 的提交创建并推送精确标签 `v<version>`。标签触发 GitHub Actions；不要把本机构建目录、签名材料或密钥提交进仓库。

GitHub Actions 的 release preflight 会再次验证 tag 是 `main` 的祖先，并运行：Python 的 Ruff、mypy baseline 和 pytest；前端的 `npm ci`、`npm test`、`npm run build`；以及 Rust 的 `cargo fmt --check` 和 `cargo test --locked`。

### Windows release (GitHub Actions)

- `.github/workflows/release.yml` 只在 Windows runner 打包正式 Windows 安装器；tag 触发时 Tauri action 创建 **draft** GitHub Release 并上传 NSIS `.exe` 与 MSI `.msi`。
- 随后 workflow 为安装器生成 `packaging/SHA256SUMS-windows.txt` 和 `packaging/python-packages-windows.json`，并上传到同一 release。Windows 当前未签名，发布说明必须保留 SmartScreen 的 “More info → Run anyway” 提示。
- `workflow_dispatch` 的 `all` 或 `windows` 只用于构建验证和下载 Actions artifact，不创建或发布 GitHub Release。

### macOS release (local Mac only)

macOS 安装包必须在具备 Developer ID 和 notarytool Keychain profile 的本机 Mac 上构建；不要把 macOS 签名或公证迁回 GitHub Actions。发布机应设置以下环境变量（只传名称和值所在的安全环境，不要写入文件或日志）：

```bash
export APPLE_SIGNING_IDENTITY='Developer ID Application: …'
export NOTARYTOOL_KEYCHAIN_PROFILE='<keychain-profile-name>'
UPLOAD_RELEASE=1 packaging/release_macos.sh
```

该脚本会在当前精确 tag 上依次运行 release doctor、安装受约束依赖、PyInstaller sidecar 构建、前端/Rust 测试、Tauri DMG 打包、sidecar 与 App 签名、notarytool 提交、公证 stapling、`hdiutil`、`codesign` 和 Gatekeeper 验证。成功后会生成 `packaging/TREE_<version>_macos.dmg`、`packaging/SHA256SUMS-macos.txt`，并在 `UPLOAD_RELEASE=1` 时上传到同一 draft release。

### Publish completion

先确认 draft release 同时包含 macOS DMG、Windows EXE/MSI 及两个 SHA-256 清单，再核对文件名、版本和校验和，最后才在 GitHub 发布该 draft。`README.md` 的下载链接、平台说明和校验和必须与最终 release assets 同步；`CHANGELOG.md` 必须记录该发布的实际变化。
