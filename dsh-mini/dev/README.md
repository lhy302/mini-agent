# DSH 极简 Agent（dsh-mini）技术说明

> **先读《开发文档.md》**（同目录）：环境搭建、源码 18 区块地图、pwsh 工具协议、
> GUI 线程模型、坑清单、测试与发布 checklist。

一个 **单文件、零依赖、复制即可运行** 的本地 Agent。功能上与 DSH 的「极简模式（minimal）」对齐：

| 项目 | 内容 |
|---|---|
| 核心定位 | **未安装 Agent 环境下的“种子安装跳板”**：专为裸机、无开发环境的 Windows 10/11 设计，双击即用，通过 AI 自主 Shell 执行实现重型 Agent 及其依赖环境的自动安装；同时作为独立极简日常 Agent |
| 界面 | 图形界面（唯一界面，纯 Win32 Unicode 控件，无控制台黑框）；TUI 相关代码保留用于源码调试 |
| 可调用工具 | 全部面向 AI 自主调用：`pwsh`（命令执行）、`str_replace_editor`（文件编辑）、`mouse_control`（鼠标控制）、`read_image`（自主读图多模态分析）、`take_screenshot`（自主截屏分析） |
| 接口协议 | 支持 OpenAI Chat Completions（文本与 Vision 多模态图片格式），可自由开启/关闭视觉支持 |
| 运行环境 | Windows 10 / Windows 11（64 位），已完全移除旧版 Win7 历史兼容包袱，**目标机器无需安装 Python、无需任何外部依赖** |

---

## 一、设计目标与定位

1. **种子引导器（Bootstrapper Agent）**：
   在干净的 Windows 10/11 系统中，用户无需手动搭建复杂的开发环境（Node.js / Python 3.12 / Git / 依赖管理）。双击运行 `dsh-mini.exe`，直接向 AI 提出需求，即可让 AI 通过 `pwsh` 自行下载并安装重型 Agent 与运行环境。
2. **轻量纯粹**：
   单文件无黑框 GUI，剥离旧版 Win7 的体积冗余，占用极少系统资源，开箱即用。

---

## 二、开箱即用

1. 把 **`dsh-mini.exe`** 单独复制到任意机器（U 盘、共享目录、桌面都行）。
2. 双击 `dsh-mini.exe`。
3. 首次启动会进入配置向导，依次填写 `base_url`、`api_key` 与 `model`。
4. 配置保存在 **exe 同目录** 的 `dsh-mini.config.json`（便携模式；若目录只读则自动改用 `%APPDATA%\dsh-mini\`）。
5. 之后每次双击即可直接对话与执行命令。

---

## 三、目录结构

交付目录（顶层，可整体复制分发）：

| 文件 | 作用 |
|---|---|
| `dsh-mini.exe` | **核心产物**，双击即用 |
| `操作指南.txt` | 中文操作指南（清配置/会话、切换对话与模型、命令速查） |
| `更新说明.txt` | 版本更新记录 |
| `dsh-mini.config.json` | 配置文件 |
| `sessions/` | 会话自动存档 |

`dev\` 子目录：

| 文件 | 作用 |
|---|---|
| `dsh-mini.py` | 源码（单文件，Python 3.8+，只用标准库） |
| `gui-e2e.py` | GUI 端到端测试（48 项断言） |
| `real-api-test.py` | 真实接口全流程测试 |
| `check-constants.py` | 静态检查：属性与快捷键漏定义 |
| `清理工具.cmd` | 双击即用的清理工具（配置/会话/日志） |
| `start-dsh-mini.cmd` | 源码启动器 |
| `build-exe.bat` / `dsh-mini.spec` | 一键打包脚本与 PyInstaller 规格文件 |
| `开发文档.md` | 开发人员与架构详细文档 |
