# TREE Packaging Decision Record

> 本文件保留桌面打包方案的关键决策与当前落地边界，不再维护阶段清单。
> 可执行的正式发布流程以 [AGENTS.md](AGENTS.md) 为准；当前版本与下载信息以
> [README.md](README.md) 为准；尚未完成且已经确认的计划只写入 [PLAN.md](PLAN.md)。

## 已确认的交付架构

- React 前端由 Tauri 打包为原生桌面 App。
- Python engine 使用 PyInstaller 构建为 `tre-engine` onedir sidecar，并作为 Tauri resource
  随安装包交付。
- sidecar 必须在目标操作系统本机构建，不能跨平台复用。
- embedding 模型不打进安装器；首次使用时由 App 准备本地 llama-server 与模型，也可使用
  外部 OpenAI-compatible embedding endpoint。
- Windows 安装器当前不签名，发布说明必须保留 SmartScreen 的运行提示。
- macOS 必须在本地发布 Mac 上使用 Developer ID 签名、公证、staple 并验证；签名材料和
  App Store Connect 私钥不进入 GitHub Actions。

## 当前发布分工

### Windows

`.github/workflows/release.yml` 在版本 tag 上运行完整 preflight，然后在 Windows runner 构建
PyInstaller sidecar 和 Tauri NSIS/MSI 安装器。tag 运行创建 draft GitHub Release，并附加
Windows SHA-256 清单与 Python 依赖清单；手动 `workflow_dispatch` 只生成 Actions artifact。

### macOS

`packaging/release_macos.sh` 只允许在干净的精确 tag 上运行。脚本构建 sidecar 和 DMG，执行
前端与 Rust 验证，完成签名、公证、stapling、`hdiutil`、`codesign` 和 Gatekeeper 检查，
最后生成版本化 DMG 与 SHA-256 清单；设置 `UPLOAD_RELEASE=1` 时上传到同一 draft release。

### 发布门禁

- 七个版本字段必须一致。
- `python3 packaging/release_doctor.py --tag v<version>` 必须通过。
- tag 必须是 `main` 的祖先。
- Python、前端和 Rust 的 release preflight 必须全部通过。
- draft release 必须同时包含 macOS DMG、Windows EXE/MSI 和两个平台的 SHA-256 清单，核对
  文件名、版本和校验和后才能发布。

完整命令、环境变量与发布完成检查见 [AGENTS.md](AGENTS.md)。
