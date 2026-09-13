# DSH 极简 Agent（dsh-mini）v1.1.5 - PE 急救定制版分支

> **极限维护环境保底方案：在完全无法安装正常 Agent 的 Windows PE 维护环境中，提供最低限度但稳定可用的离线急救与系统运维能力**

| 项目 | 内容 |
|---|---|
| 核心定位 | **维护急救环境保底 Agent（Emergency / Rescue Agent）**：专为优启通、微PE、Edgeless、纯净 WinPE 等维护系统打造。在完全无法安装现代化重型 Agent 的无网络/弱网络/只读离线环境下，提供单文件直跑的最低限度自主 Shell 运维、引导修复与系统急救能力 |
| 界面 | **图形界面（唯一界面）**：菜单栏 + 对话区 + 输入框，纯 Win32 控件（ctypes），无控制台黑框，低内存占用（RAM 盘防爆保护） |
| 可调用工具 | 核心面向离线维护：`pwsh`（PowerShell 会话与原生 CMD 降级双引擎）、`str_replace_editor`（文件查看/创建/按块替换/插入） |
| 接口协议 | 支持标准 OpenAI Chat Completions（`POST {base_url}/chat/completions`，支持 SSE 流式） |
| 运行环境 | Windows PE 预安装维护环境、Windows 7 SP1 及以上，**单文件自带运行时与依赖，无需 Python，无任何外部依赖** |
| PE 离线急救 | **自动识别 PE 环境**、全盘驱动器与离线 Windows 扫描、UEFI/ESP 引导识别、免证书校验、**内嵌精简 PowerShell 7 + 原生 CMD 降级双引擎**、RAM 盘写保护 |
| 源码 | 单文件 `dev/dsh-mini.py`，只用 Python 标准库，无第三方依赖，无网络回传 |

---

## 一、为什么需要 PE 急救定制版？

当电脑遭遇系统引导崩溃、蓝屏死机、分区损坏或勒索锁机时，用户只能通过 U 盘启动进入 **Windows PE（预安装维护环境）**。
在 PE 这种精简、临时、缺乏绝大多数 Windows 现代组件（无 WebView2、无完整 .NET、无 Edge、注册表精简）的环境下，现代主流 Agent 系统（依赖 Node.js/Electron/完整系统库）**100% 无法安装或运行**。

**`dsh-mini PE 急救定制版` 为绝境运维提供了唯一的 Agent 方案**：
1. **单文件直接在 RAM 盘运行**：无需安装，双击即开，自动避开 X 盘 RAMDisk 空间写满风险；
2. **免证书网络校验**：内置 SSL/TLS 证书宽松模式，解决 PE 下根证书陈旧导致无法连接大模型网关的问题；
3. **PE 专有诊断与引导识别**：开机自动扫描并识别挂载的物理硬盘、各驱动器卷标、离线 Windows 目录（如 `D:\Windows`）及 ESP 引导分区；
4. **最低限度但可靠的急救能力**：AI 可自主执行 `bcdedit`、`diskpart`、`dism`、`sfc` 等离线修复命令，或直接通过 `str_replace_editor` 查看与改写离线系统的配置文件，帮助管理员快速恢复崩溃系统。

---

## 二、直接用

1. 把 **`dsh-mini.exe`** 单独复制到 WinPE 的桌面或 U 盘中，双击运行。
2. **第一次使用会自己弹出设置**：填入接口地址与 API Key。
3. 即可在 PE 下通过自然语言让 AI 自主排查硬盘分区、修复系统引导或修改离线配置。
