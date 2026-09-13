# DSH 极简 Agent（dsh-mini）- Linux 专用分支开发与技术指南

> **配套技术文档**：
> * 《开发文档.md》（同目录下）：详细阐述源码 18 个功能区块、Shell 持久会话通信协议、GUI 线程架构与历史踩坑清单；
> * 《../操作指南.txt》：面向用户的完整功能速查与使用手册；
> * 《../更新说明.txt》：版本演进史与 Linux 专用分支重构说明。

一个 **单文件、零依赖、复制即用、双击即开** 的 Linux 本地 Agent，功能与 DSH 的「极简模式（minimal）」完全对齐。

| 项目 | 内容 |
|---|---|
| **核心定位** | **Linux 环境下的种子引导 Agent（Seed / Bootstrapper Agent）**：在无任何开发环境的纯净 Linux 机器上，双击即开，自主执行 bash 脚本全自动配置环境并安装部署重型 Agent；亦可作为独立的轻量日常 Agent |
| **运行方式** | **单文件且双击即开**：桌面图标 `dsh-mini.desktop`、启动器 `start-dsh-mini.sh` 或单文件程序 `dsh-mini` |
| **界面体系** | **原生图形界面（唯一主界面）**：菜单栏 + 滚动对话区 + 自增高多行输入框 + 状态栏，无控制台黑框 |
| **可调用工具** | 全部面向 AI 自主调用：`pwsh/bash`（命令执行）、`str_replace_editor`（文件编辑）、`mouse_control`（鼠标控制）、`read_image`（自主读图多模态分析）、`take_screenshot`（自主截屏分析） |
| **接口协议** | 支持 OpenAI Chat Completions（文本流式与 Vision 多模态图片格式），可自由开启/关闭视觉支持 |
| **目标环境** | Linux x86_64（Ubuntu / Debian / CentOS / Fedora 等主流发行版），**目标机器无需安装 Python、无需任何第三方依赖** |
| **源码体系** | 单文件 `dev/dsh-mini.py`，仅使用 Python 标准库，无网络回传，无任何外部库绑定 |

---

## 一、开箱即用与核心产物

### 1. 核心交付文件

交付目录（根目录）包含以下文件：

| 文件名 | 作用与特点 |
|---|---|
| **`dsh-mini`** | **程序本体**（Linux 64 位单文件 ELF 二进制程序，内置完整 Python 运行时与 GUI 库，双击即用） |
| **`dsh-mini.desktop`** | **桌面快捷方式**（置于 Linux 桌面后双击直接弹出图形窗口，符合 FreeDesktop 规范） |
| **`start-dsh-mini.sh`** | **便捷启动脚本**（支持在终端快速以 `./start-dsh-mini.sh` 启动，带参数透传） |
| **`操作指南.txt`** | 面向用户的详细中文教程（配置向导、多对话切换、快捷键、Linux 常见排障） |
| **`更新说明.txt`** | Linux 专用分支版本改动记录与技术指标说明 |
| **`README.md`** | 项目基础公开说明 |
| **`dsh-mini.config.json`** | 配置文件（初次启动由向导自动写入，随改随生效） |
| **`dev/`** | 完整开发源码包与一键打包工具集 |

运行时动态生成（不包含在发行包中）：
* `sessions/`：会话存档目录（保存历史交流 JSON 数据）；
* `dsh-mini-gui.log`：界面全量日志（按行同步落盘，调试排障首要依据）；
* `dsh-mini-diagnose.txt`：自检与诊断输出（独立窗口专用，不污染对话历史）；
* `dsh-mini-error.log`：异常崩溃日志（仅在发生崩溃时生成）。

---

## 二、运行与交互

### 1. 双击启动与首次配置
1. 将 `dsh-mini` 或 `dsh-mini.desktop` 放置于桌面或任意工作目录；
2. 直接双击运行：程序会自动检测环境并打开主窗口；
3. **首次运行自动居中弹出设置向导**：
   * 自动聚焦输入框，输入 `base_url`（如 `https://api.deepseek.com`）并回车；
   * 输入 `api_key`（输入内容默认以 `*` 掩码显示，支持明文勾选，支持 `Ctrl+V` 粘贴）并回车；
   * 确定后自动向接口请求 `/models`，并在弹窗中列出可用模型列表，双击选择模型即可进入聊天；
   * 后续可随时按 **F2**（接口与密钥）或 **F3**（选择模型）修改。

### 2. 快捷键一览（手感与操作习惯深度对齐）

| 按键 | 功能 | 说明 |
|---|---|---|
| `Enter` | 发送消息 | 在输入框内敲回车发送；单行与多行均生效 |
| `Shift+Enter` / `Ctrl+J` | 插入换行 | 多行输入模式，输入框高度会随行数自动伸缩（2~8 行） |
| `Esc` | 打断当前输出 | 停止模型文本流式生成或正在执行的 Shell 命令（等同界面「停止」按钮） |
| `↑` / `↓` | 历史记录翻阅 | 单行状态下翻阅上一条/下一条已发送的消息 |
| `Ctrl+C` | 复制 / 清空 / 退出 | 有选中文字时为复制；未选中文字时清空输入框；空框时再按一次退出 |
| `Ctrl+D` | 快速退出 | 退出程序（自动保存有内容的对话） |
| `Ctrl+L` / `F7` | 新对话 | 自动保存当前对话并清空显示区，开启全新会话 |
| `Ctrl+S` | 手动保存 | 将当前对话立即序列化落盘到 `sessions/` |
| `Ctrl+O` | 打开会话 | 弹出模态选择框，查看历史会话时间、模型和轮数，点击一键切换 |
| `Ctrl+P` | 发送图片 | 弹出图片路径与提示词输入框，按 OpenAI 多模态 Vision 格式发送 |
| `F1` | 操作指南 | 打开操作指南文本文件或关于弹窗 |
| `F2` | 接口与密钥 | 调出设置向导重新配置 base_url 与 API Key |
| `F3` | 选择模型 | 重新扫描并调出模型选择对话框 |
| `F4` | 显示/隐藏思考过程 | 切换模型思维链（`…` 开头内容）的展示状态（实时回写配置文件） |
| `F5` | 运行离线自检 | 在独立窗口中执行 **92 项离线自检断言** |
| `F6` | 运行 Shell 诊断 | 在独立窗口中检查 Linux Shell（Bash/pwsh）会话可用性与探针 |
| `F8` | 复制全部内容 | 将对话区的所有文字内容拷贝至系统剪贴板 |

---

## 三、Linux 专用工具架构

### 1. pwsh / bash 持久会话引擎
* **stdin 管道协议**：不同于常规的 `subprocess.run`（单次退出无法保持状态），`PersistentShell` 启动 `/bin/bash --norc` 后台常驻进程；
* **命令包装传输**：命令以 `@` 起始并用 `\x1e` 编码多行，输入给 Loader 执行；
* **状态保持**：`cd /tmp`、`export MY_ENV=1`、自定义 Shell 函数与后台进程在多轮交互中持续生效；
* **退出码透传**：捕获 `$?` 并通过 `__DSHMINI_<tag>_END:<code>` 精准传递给大模型；
* **优雅降级**：若持久会话启动超时或异常，自动平滑回退至 `oneshot` 单命令模式，并在系统提示词中通知模型。

### 2. str_replace_editor 文件编辑器
* 专为大模型精准编辑文件设计，提供 `view`, `create`, `str_replace`, `insert` 四个子命令；
* 严格校验 Linux 绝对路径规范（必须以 `/` 开头）；
* 内置编码自适应识别机制，支持 UTF-8、BOM、GBK、CP936 以及换行符格式（LF / CRLF）的原样保持。

### 3. mouse_control 鼠标自主控制
* 集成 Linux `xdotool`，提供精准的光标位置移动（`move`）、按键点击与长按释放（`click`/`down`/`up`）、双击（`double_click`）、滚轮滚动（`scroll`）及屏幕坐标分辨率查询（`position`）；
* 在无 X11 图形环境（或自动化脚本中）具备完备的仿真降级能力，不会引发异常。

### 4. take_screenshot 截屏分析与 read_image 视觉读取
* **截屏分析**：调用 Linux 原生截屏工具链（优先 `scrot`，兼容 `gnome-screenshot`、`grim` 与 ImageMagick `import`），截屏后以临时 PNG 存盘并自动作为上下文注入多模态模型；
* **视觉读取**：支持读取本地 PNG、JPG、JPEG、WEBP、GIF、BMP 图片，自动编码为 Base64 Data URL 注入上下文。

---

## 四、从源码重新打包与自检

在 `dev/` 目录中提供了一键打包与回归验证脚本 `build-linux.sh`：

```bash
cd dev
bash build-linux.sh
```

构建脚本将执行：
1. 检测本机 Python 3 与 PyInstaller；
2. 依据 `dsh-mini-linux.spec` 规范文件，将 `dsh-mini.py` 及标准库 Tkinter、多模态等全部资产打包为一个单文件二进制 `../dsh-mini`；
3. 赋予可执行权限；
4. 自动调用新构建的单文件程序执行 **92 项离线自检套件**（`../dsh-mini --selftest`），确保质量合格方才收尾。

---

## 五、测试与验证规范

每次代码更新后，建议按顺序执行以下四重验证：

1. **静态属性检查**：
   ```bash
   python3 dev/check-constants.py dev/dsh-mini.py
   ```
2. **离线综合自检（92 项断言）**：
   ```bash
   xvfb-run -a python3 dev/dsh-mini.py --selftest
   ```
3. **Shell 环境诊断**：
   ```bash
   python3 dev/dsh-mini.py --shellcheck
   ```
4. **单文件二进制直接测试**：
   ```bash
   xvfb-run -a ./dsh-mini --gui-autoclose 2
   ```
