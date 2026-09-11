# DSH 极简 Agent（dsh-mini）v1.1.5

一个 **单文件、零依赖、复制即用** 的本地 Agent，功能与 DSH 的「极简模式（minimal）」对齐。

| 项目 | 内容 |
|---|---|
| 界面 | **图形界面（唯一界面）**：菜单栏 + 对话区 + 输入框，纯 Win32 控件（ctypes），无控制台黑框 |
| 可调用工具 | 只有 2 个：`pwsh`（PowerShell 会话）、`str_replace_editor`（view / create / str_replace / insert） |
| 接口协议 | 只支持常规 OpenAI Chat Completions（`POST {base_url}/chat/completions`，支持 SSE 流式） |
| 运行环境 | Windows 7 SP1 及以上、**Windows PE 预安装维护环境（优启通/微PE/Edgeless/纯净WinPE等）**，**单文件自带运行时与依赖，无需 Python，无外部依赖** |
| PE 离线急救 | **自动识别 PE 环境**、全盘驱动器与离线 Windows 扫描、UEFI/ESP 引导识别、免证书校验、**内嵌精简 PowerShell 7 + 原生 CMD 降级双引擎**、RAM 盘防爆保护 |
| 源码 | 单文件 `dev/dsh-mini.py`，只用 Python 标准库，无第三方依赖，无网络回传 |

> **为什么只有图形界面**：Win7 的控制台是旧式实现，除了编码崩溃（v1.0.2 修复），
> 还有「中文显示成两个」「字体突然变大」这类**渲染层**的怪现象，程序管不着。
> GUI 用 Edit 控件（Unicode 原生）绕开整个控制台，并把界面每一行写进
> `dsh-mini-gui.log`，所以「命令到底有没有执行成功」看日志最准。


## 一、直接用

1. 把 **`dsh-mini.exe`** 单独复制到任意机器（U 盘、桌面都行），双击运行。
2. **第一次使用会自己弹出设置**：光标已经在输入框里，直接填「接口地址」，回车 → 填「API Key」，
   回车 → 自动接着弹出模型列表（来自接口的 `/models`，也可以直接手输模型名；取消=保持当前）。
   以后想改随时按 **F2**（或菜单 设置 → 接口与密钥）。
3. 在最下面的输入框里打字，**回车发送**。
   模型的**思考过程**会实时显示出来（`…` 开头，真的在"想"的时候能看到，觉得跑偏了就按 `Esc`
   或点「停止」按钮打断）；正文以 `●` 开头。每轮回复后还会显示 token 用量与（缓存命中/未命中）：
   多轮工具调用时前缀不变的部分走服务端缓存（DeepSeek 系约 1/10 计价），token 消耗可控。
   命令执行过程会实时显示：`» pwsh` + 命令 + 实时输出 + `√/× 完成/失败` + 命令真实输出。
4. 配置保存在 exe 同目录的 `dsh-mini.config.json`（便携模式；目录只读时自动改用 `%APPDATA%\dsh-mini\`）。

### 快捷键（沿用命令行版的手感）

| 按键 | 功能 |
|---|---|
| `Esc` | 打断当前输出 / 停止正在执行的命令（等同点「停止」按钮） |
| `Enter` | 发送；`Ctrl+J` 或 `Shift+Enter` 换行（可写多行） |
| `↑` / `↓` | 翻阅历史输入 |
| `Ctrl+C` | 有选中=复制；没选中=清空输入；再按一次=退出 |
| `Ctrl+D` / `Ctrl+L` | 退出 / 清空显示 |
| `Ctrl+S` | 保存会话 |
| `Ctrl+O` | 打开会话（多对话切换） |
| `F1`..`F8` | 操作指南 / 接口与密钥 / 选择模型 / **显示思考过程** / 离线自检 / pwsh 诊断 / 新对话 / 复制全部 |

### 菜单（常驻，不占对话区）

* **会话(S)**：新对话 / 打开会话…（多对话切换，切换前自动保存当前会话）/ 保存会话 / 复制全部 / 退出
* **工具(T)**：离线自检（81 项）/ pwsh 诊断 / 打开诊断日志 / 打开界面日志 / 清空界面日志
  —— 自检与诊断的输出在**独立窗口**里，同时写入 `dsh-mini-diagnose.txt`，不会冲掉对话
* **设置(C)**：选择模型（自动扫描 `/models` + 可手输）/ 接口与密钥 / 显示思考过程（勾选开关）/ 打开工作目录
* **帮助(H)**：操作指南 / 关于

先确认程序没问题（都不联网）：菜单「工具」→ 离线自检（81 项）/ pwsh 诊断，
或用命令行参数 `dsh-mini.exe --selftest`（无控制台，会开窗口并自动执行）。

完整用法见 **`操作指南.txt`**；本版改动见 **`更新说明.txt`**。


## 二、这个目录里有什么

| 文件 | 作用 |
|---|---|
| `dsh-mini.exe` | **程序本体**（图形界面，唯一版本） |
| `操作指南.txt` | 完整使用教程（菜单、快捷键、配置、常见问题） |
| `更新说明.txt` | v1.1.5 更新说明与 Win7 排查指引 |
| `dsh-mini.config.json` | 配置模板（`api_key` 留空，首次运行会让你填） |
| `dev/` | 源码与打包脚本（不影响运行，可以删） |

运行后按需生成：`sessions\`（保存的会话）、`dsh-mini-gui.log`（界面全量日志）、
`dsh-mini-diagnose.txt`（自检/诊断输出）、`dsh-mini-error.log`（只在崩溃时生成）。

`dev/` 子目录：

| 文件 | 作用 |
|---|---|
| `dsh-mini.py` | **源码**（单文件，Python 3.8+，仅标准库） |
| `gui-e2e.py` | GUI 端到端测试（48 项断言，假网关，不联网） |
| `real-api-test.py` | 真实接口端到端测试（用你自己的网关跑一遍全流程） |
| `check-constants.py` | 静态检查：self.XXX 有没有漏定义（快捷键失效那类坑） |
| `dsh-mini.spec` | PyInstaller 配置（`console=False`，打出来就是无控制台的 GUI） |
| `dsh-mini.version.txt` | 版本资源（写进 exe 属性） |
| `build-win7-exe.bat` | 一键打包 |
| `dsh-mini.config.example.json` | 配置示例（全部字段） |
| `start-dsh-mini.cmd` | 未打包时用源码启动的启动器 |
| `清理工具.cmd` | 双击即用的清理工具（清配置/会话/日志） |
| `README.md` | 开发与技术文档（Win7 兼容原理、协议细节、验证记录） |


## 三、自己从源码打包

需要 **Python 3.8.x**（3.8 是最后一个支持 Windows 7 的 Python）：

```bat
dev\build-win7-exe.bat
```

脚本会自动安装 `pyinstaller==5.13.2`（PyInstaller 6.x 已放弃 Win7），
按 `dev\dsh-mini.spec` 打成单文件无控制台 exe，输出到本目录。

打包时会从打包机的 `C:\Windows\System32\downlevel` 收集 UCRT 运行库并塞进 exe，
这是「复制到干净的 Windows 7 SP1 也能跑」的关键。

直接跑源码也可以（无需打包）：

```bat
cd dev
python dsh-mini.py
```

### 命令行参数（都会打开窗口并自动执行对应动作）

```bat
dsh-mini.exe                        :: 图形界面
dsh-mini.exe --selftest             :: 开窗并自动跑 81 项自检
dsh-mini.exe --shellcheck           :: 开窗并自动跑 pwsh 诊断
dsh-mini.exe --models               :: 开窗并弹出模型选择
dsh-mini.exe --setup                :: 开窗并打开接口与密钥设置
dsh-mini.exe -p "提示词"            :: 开窗并自动发送这条消息
dsh-mini.exe --config D:\my.json    :: 指定配置文件
dsh-mini.exe --cwd D:\project       :: 指定工作目录
dsh-mini.exe --model 名称           :: 指定模型
```


## 四、Windows 7 兼容史（为什么要做成 GUI）

* **v1.0.1**：Win7 SP1 自带 PowerShell 2.0，旧协议（`-Command -` + stdin）在 2.0 上
  子进程毫无反应，每条命令卡到超时。改为自带 loader 协议 + 启动握手 +
  失败自动降级「单命令模式」。
* **v1.0.2**：Win7 控制台按 cp936 严格编码，界面里的 `»` `›` 不在 GBK 里，
  一打印就 `UnicodeEncodeError` 崩掉。加输出层编码降级 + 崩溃日志。
* **v1.0.3 / v1.1.0**：干脆改用图形界面（Unicode 控件），不再经过控制台；
  并补齐菜单、快捷键、模型选择、多会话切换。
* 诊断：GUI 里菜单「工具 → pwsh 诊断」，会把候选 PowerShell、两种执行方式的
  实测结果全部列出来（同时写入 `dsh-mini-diagnose.txt`）。


## 五、杀软误报说明

PyInstaller 打包的单文件 exe 偶尔会被 Windows Defender 之类的杀软按启发式误判
（例如 `Trojan:Win32/Bearfoos.A!ml`，这是机器学习误报，不是真的报毒）。
本项目已刻意去掉了容易被误判的特征：不使用 base64 解码执行、不读文件动态执行，
并写入了正规的版本资源。

若仍被拦截，把 exe 所在目录加入杀软白名单即可；也可以直接用上面的
`dev\build-win7-exe.bat` 从源码自行重新打包 —— 源码全部公开可查。
