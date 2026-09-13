# DSH 极简 Agent（dsh-mini）

> **先读《开发文档.md》**（同目录）：环境搭建、源码 18 区块地图、pwsh 工具协议、
> GUI 线程模型、坑清单、测试与发布 checklist。本文档是更早的技术底稿 + 验证记录，
> 两者配合看。当前形态：**图形界面是唯一界面**（无控制台打包）。

一个 **单文件、零依赖、复制即可运行** 的本地 Agent。功能上与 DSH 的「极简模式（minimal）」对齐：

| 项目 | 内容 |
|---|---|
| 核心定位 | **维护急救环境保底 Agent（Emergency / Rescue Agent）**：专为 Windows PE 维护系统打造，在完全无法安装现代重型 Agent 的极端环境下，提供最低限度但稳定可用的本地离线运维与急救能力 |
| 界面 | 图形界面（唯一界面，无控制台黑框）；TUI 相关代码保留用于源码调试 |
| 可调用工具 | 只有 2 个：`pwsh`（持久 PowerShell 会话）、`str_replace_editor`（view / create / str_replace / insert） |
| 接口协议 | 只支持常规 OpenAI Chat Completions（`POST {base_url}/chat/completions`，支持 SSE 流式） |
| 运行环境 | Windows 7 SP1 及以上（64 位 exe），**目标机器无需安装 Python、无需任何依赖** |

---

## 一、开箱即用（目标机器零前置环境）

1. 把 **`dsh-mini.exe`** 单独复制到任意机器（U 盘、共享目录、桌面都行）。
2. 双击 `dsh-mini.exe`。
3. 首次启动会进入配置向导，依次填写：
   * `base_url`：OpenAI 兼容接口地址，例如 `https://api.deepseek.com`。程序会自动补成 `.../chat/completions`；
     如果网关的端点不在 `/v1` 下，直接填完整端点（形如 `https://网关地址/xxx/chat/completions`）最稳妥
   * `api_key`：你的密钥
   * `model`：模型名，例如 `deepseek-chat`
4. 配置保存在 **exe 同目录** 的 `dsh-mini.config.json`（便携模式；若 exe 所在目录只读，则自动改用 `%APPDATA%\dsh-mini\`）。
5. 之后每次双击即可直接对话。

> exe 内已捆绑 Python 3.8 运行时、`vcruntime140.dll` 与整套 Universal CRT（`api-ms-win-crt-*.dll` + `ucrtbase.dll`），
> 因此干净的 Windows 7 SP1 即使没装 KB2999226（UCRT 更新）也能直接运行。

### 目录里都有什么

交付目录（顶层，可整体复制分发）：

| 文件 | 作用 |
|---|---|
| `dsh-mini.exe` | **核心产物**，双击即用 |
| `操作指南.txt` | 中文操作指南（清配置/会话、切换对话与模型、命令速查） |
| `dsh-mini.config.json` | 配置（地址/密钥/模型） |
| `sessions/` | 会话自动存档（可在配置里关掉） |

`dev\` 子目录（不影响运行，可以删）：

| 文件 | 作用 |
|---|---|
| `dsh-mini.py` | 源码（单文件，Python 3.8+，只用标准库） |
| `gui-e2e.py` | GUI 端到端测试（26 项断言；可测源码，也可 `--exe` 测打包产物） |
| `清理工具.cmd` | 双击即用的清理工具，自动定位到 exe 所在目录 |
| `start-dsh-mini.cmd` | 没打包时的启动器（优先找上层 exe，其次找本机 Python） |
| `dsh-mini.config.example.json` | 配置示例 |
| `build-win7-exe.bat` / `dsh-mini.spec` | 重新打包 Win7 可用 exe 的脚本与 PyInstaller 配置 |

---

## 二、用法

### 图形界面版（推荐，尤其 Windows 7）

```bat
dsh-mini-gui.exe                :: 双击即可，无控制台黑框
dsh-mini-gui.exe --gui          :: 命令行版 exe 也能开界面
dsh-mini-gui.exe --gui-autoclose 20   :: 冒烟测试：开窗口、自动跑 pwsh 诊断、20 秒后自毁
```

* 纯 **ctypes + Win32 控件**（Edit / Button / Static），零第三方依赖，不依赖 tkinter；
* Edit 控件是 Unicode 原生的：**中文、符号、字体全部不经过控制台代码页**，
  从根上绕开 Win7 控制台的编码与渲染问题；
* 界面每一行同步写进 exe 同目录的 **`dsh-mini-gui.log`**（UTF-8），
  所以「命令到底执行成功没有」看日志最准，不受屏幕影响；
* 输入框回车发送；按钮：发送 / 自检 / 诊断 / 设置 / 清空 / 复制全部 / 打开日志；
* 无控制台时给子 PowerShell 加 `CREATE_NO_WINDOW`（不闪黑框、互不干扰控制台状态）；
* windowed 打包（`console=False`）下 `sys.stdout is None`，程序会自动进入 GUI 模式。

### 命令行版（TUI）

```bat
dsh-mini.exe                                  :: 交互模式
dsh-mini.exe -p "列出当前目录的文件"           :: 单次执行（脚本化 / 测试）
dsh-mini.exe --setup                          :: 重新填写密钥与地址
dsh-mini.exe --config D:\my.json              :: 指定配置文件
dsh-mini.exe --model deepseek-chat            :: 临时覆盖模型
dsh-mini.exe --pick-model                     :: 启动时强制弹出模型选择
dsh-mini.exe --no-pick-model                  :: 启动时跳过模型选择
dsh-mini.exe --cwd D:\project                 :: 指定工作目录
dsh-mini.exe --selftest                       :: 离线自检（不联网，65 项）
dsh-mini.exe --shellcheck                     :: 离线诊断 pwsh 工具（Win7 排障用）
dsh-mini.exe --shell-mode oneshot             :: 强制单命令模式（每条命令一个进程）
dsh-mini.exe --shell-timeout 60000            :: pwsh 单条命令超时（毫秒）
dsh-mini.exe --no-color --no-stream           :: 关颜色 / 关流式
```

### 交互模式里的命令

| 命令 | 说明 |
|---|---|
| `/help` | 帮助 |
| `/tools` | 查看可调用工具 |
| `/models` | 扫描接口可用模型并切换（也可直接输入模型名） |
| `/config` | 查看当前生效配置（密钥打码，末行显示 pwsh 实际状态） |
| `/shellcheck` | 离线诊断 pwsh（命令跑不动时先跑这个） |
| `/model <名称>` | 临时切换模型 |
| `/cwd <路径>` | 切换工作目录（会重启 pwsh 会话） |
| `/clear`、`/new` | 清空上下文 |
| `/save [路径]` | 保存会话 |
| `/resume [list\|序号]` | 恢复历史会话 |
| `/exit`、`/quit` | 退出 |

### 输入技巧

* `↑` `↓` 翻历史输入，`←` `→` `Home` `End` 行内编辑
* `Ctrl+V` 粘贴剪贴板（内置 ctypes 实现，不依赖任何库）
* `Ctrl+J` 输入换行（多行消息）；直接粘贴多行文本也可以
* `Ctrl+C` 清空当前输入；空行时按两次退出
* `Ctrl+Z` / `Ctrl+D` 退出

---

## 三、两个工具的行为

### 1. `pwsh`（持久 PowerShell）

```json
{"command": "Get-ChildItem | Select-Object -First 10"}
```

* **一个长驻 PowerShell 进程**，变量、`Set-Location`、函数、后台作业在多次调用之间保持。
* 优先使用 `pwsh.exe`（PowerShell 7），找不到就用 `powershell.exe`（Win7 自带）。
* 命令以 Base64 单行协议送入子进程的 stdin，由**程序自带的一段 loader**（`while` 循环 +
  `[Console]::In.ReadLine()`）负责读取与执行；标记用 `[Console]::Out.WriteLine` + `Flush`
  显式写出，因此多行脚本、引号、中文都不会被破坏，也不依赖宿主怎么缓冲输出。
* **兼容 PowerShell 2.0 ~ 7.x**：启动后先握手（一条探针命令，默认 20 秒），
  握手不通就自动降级为「单命令模式」（每条命令一个独立进程，`-Command` 直跑），
  命令一定有结果，不会再出现「一发命令就无声卡死」。
* 退出码：`[exit code: N]`；超时（默认 300 秒）会杀掉进程并提示会话已重置。
* 输出上限 16000 字符，超出追加 `<response clipped>...` 标记（与 DSH 一致）。
* 输出编码自适应：UTF-8 优先，失败回退本机代码页（中文 Windows 上的 GBK/cmd 输出也能正确显示）；
  子进程环境注入 `PYTHONIOENCODING=utf-8`，Python 脚本的中文输出同样正常。
* 子进程的错误流是**纯文本**（刻意不用 `-EncodedCommand`，它会把错误序列化成 CLIXML，
  让输出里混进 `#< CLIXML ...<Objs ...` 垃圾）。
* 不支持交互式 stdin（命令等待输入会一直等到超时），与 DSH 的行为一致。
* 出问题先跑 `dsh-mini.exe --shellcheck`：它会列出候选 PowerShell、逐个探测、
  并分别实测「持久会话」与「单命令模式」。

#### Win7 上为什么曾经一条命令都跑不了

Win7 SP1 自带的是 **Windows PowerShell 2.0**。v1.0.0 用的是
`powershell.exe -NoLogo -NoProfile -NoExit -Command -` + 往 stdin 写命令的老写法，
依赖「宿主逐行读取 stdin 并执行」这一行为；在 2.0 上这个行为不成立，
子进程既不执行也不报错、更没有任何输出，于是每条命令都卡到 5 分钟超时
（界面表现就是停在一行 `» pwsh ...` 上不动）。

v1.0.1 的三层保险：

1. **自带协议**：用 `-Command` 启动一段内置 loader，自己读 stdin、自己写标记、自己 `Flush`；
2. **握手 + 自动降级**：默认 20 秒握手，失败就切到单命令模式（`-Command` 直跑，
   退出码用 `$LASTEXITCODE` 如实带回），并在界面和工具结果里说明状态不再保持；
3. **诊断命令**：`--shellcheck` / `/shellcheck` 把每一步的真实结果打出来，
   不用再靠猜。


### 2. `str_replace_editor`

| 命令 | 行为 |
|---|---|
| `view` | 文件：带行号显示（`cat -n` 风格）；目录：列出 2 层内非隐藏文件，排除 `node_modules`、`__pycache__` |
| `create` | 新建文件；文件已存在则报错，绝不覆盖 |
| `str_replace` | `old_str` 必须**唯一且逐字**匹配，否则报错 |
| `insert` | 在第 `insert_line` 行**之后**插入 `new_str`（`0` 表示文件开头） |

* 路径必须是**绝对路径**（`C:\...` 或 `\\server\share\...`）。
* 输出上限 16000 字符，超出追加截断标记。
* 编码与换行会被保留：UTF-8 / UTF-8 BOM / UTF-16 / GBK 文件读写后编码不变，CRLF 文件不会被改成 LF。
* 与 DSH 的差异：DSH 只按 UTF-8 读文件，这里额外兼容 GBK，并且 `old_str` 按 LF 归一化后匹配（CRLF 文件不必手动写 `\r`）。

---

## 四、配置文件

`dsh-mini.config.json`（UTF-8，放在 exe 同目录；也可以只放需要覆盖的字段）：

```json
{
  "base_url": "https://api.deepseek.com",
  "api_key": "sk-...",
  "model": "deepseek-chat",
  "temperature": 0,
  "max_tokens": 0,
  "stream": true,
  "request_timeout": 300,
  "retries": 3,
  "include_usage": false,
  "extra_headers": {},
  "extra_body": {},
  "model_picker": true,
  "system_prompt": "You are a helpful software engineer assistant.",
  "include_env_note": true,
  "max_output_chars": 16000,
  "shell_timeout_ms": 300000,
  "shell_exe": "",
  "shell_mode": "auto",
  "shell_probe_timeout_ms": 20000,
  "shell_encoding": "auto",
  "cwd": "",
  "max_tool_rounds": 60,
  "editor": "auto",
  "color": "auto",
  "show_reasoning": true,
  "save_sessions": true,
  "show_live_output": true
}
```

常用项：

* `shell_exe`：指定 PowerShell 路径（留空自动探测；指定了却用不上会明确提示）。
* `shell_mode`：`auto`（默认，优先持久会话、失败自动降级）/ `persistent`（强制持久，失败即报错）/
  `oneshot`（每条命令一个独立进程，最保守）。
* `shell_probe_timeout_ms`：长驻会话握手超时，默认 20000；老机器 PowerShell 启动慢可调大。
* `shell_encoding`：`auto` / `utf-8` / `gbk` / `cp936`，强制子进程输出解码方式。
* `model_picker`：每次启动是否扫描 `GET {base}/models` 让你选一次，默认 `true`；回车保持当前、输序号选清单项、输别的当模型名。
* `extra_headers` / `extra_body`：给特殊网关加自定义请求头或厂商私有字段。
* `include_env_note: false` → 系统提示词完全等同于 DSH 极简模式的固定人格（只有一句 `You are a helpful software engineer assistant.`）。
* 环境变量可覆盖密钥等：`DSH_MINI_API_KEY` / `OPENAI_API_KEY`、`DSH_MINI_BASE_URL` / `OPENAI_BASE_URL`、`DSH_MINI_MODEL` / `OPENAI_MODEL`。

---

## 五、重新打包（生成 Win7 可用的 exe）

```bat
:: 需要先装 Python 3.8.x（3.8 是最后一个支持 Windows 7 的 Python）
dev\build-win7-exe.bat
```

脚本会：安装 `pyinstaller==5.13.2`（PyInstaller 6.x 已放弃 Win7 与 Python 3.8）→ 按 `dsh-mini.spec` 打成 onefile
→ 输出到 **上一层交付目录**（`..\dsh-mini.exe`）。

`dsh-mini.spec` 会从打包机的 `C:\Windows\System32\downlevel` 收集 UCRT 运行库并塞进 exe，
这是「复制到干净 Win7 也能跑」的关键。

---

## 六、验证记录（本机实测）

### v1.1.5（修「设置框弹完以后整个程序点不动」）

用户反馈：设置框关掉、跳到模型列表之后，鼠标点不了任何东西、光标卡在 I 型、
连标题栏 ✕ 都点不了。**趁卡住时抓现场**（只读查询，不动用户的程序）得到的事实：

* 线程响应正常（`SendMessageTimeout(WM_NULL)` 及时返回）、没有鼠标捕获、
  没有菜单/移动循环、对话框可见且在**前台**、列表里有 1 项、主窗被禁用（模态，正常）；
* 但 **`GetGUIThreadInfo.hwndFocus` = 对话框窗口自己**（不是列表/编辑框）→ 键盘全废。

机制：模态期间主窗被禁用是对的，但对话框**没能真正抢到前台**时（本进程不在前台，
系统拒绝 `SetForegroundWindow`）它可能被别的窗口盖住 → 用户看到"程序死了"；
而焦点落在对话框自己身上（激活消息异步把 `SetFocus` 顶掉）→ 键盘也没反应。
另外对话框窗口类的 `hCursor` 是 NULL → 鼠标停在对话框空白处会一直保持 I 型。

修复（五处，全部有端到端断言）：

| 修法 | 断言 |
|---|---|
| 弹出时 `SetWindowPos(HWND_TOPMOST)`，关闭时撤掉 | 对话框带 `WS_EX_TOPMOST` |
| 窗口类补箭头光标（`cursor=True`） | 自检查 `GCLP_HCURSOR` == IDC_ARROW |
| `WM_ACTIVATE` 里重新聚焦主要控件 | 激活 0.8s 后焦点仍在编辑框/列表 |
| 关窗后 `PostMessage(WM_APP_REFOCUS)` 补焦点 | 焦点回到输入框且马上能发消息 |
| 模态期间主窗标题加"请先完成弹出的对话框" | 标题有提示、结束还原 |

验证：`--selftest` **81/81**、`dev\gui-e2e.py` **48/48**（新增 10 项）、
`dev\real-api-test.py` **8/8**（真实网关）、`--gui-autoclose` 冒烟退出码 0。

### v1.1.4（快捷键全灭的元凶 + 思考过程 + 设置流程 + 输出贴底）

用户反馈四条，实测查到的东西比现象更狠：

1. **所有快捷键从来没生效过**：`_handle_key` 第一行 `if key == self.VK_ESCAPE:`
   而 `DshGui` 里**从来没定义 VK_ESCAPE** → 每次按键都在第一行抛 AttributeError，
   被 ctypes 窗口过程回调静默吞掉（windowed 打包后 stderr 无人可见）。
   受影响的是回车发送 / Esc 打断 / F1..F8 / Ctrl+L·S·D·O / ↑↓ —— 用户只能用鼠标点按钮。
   修复：补齐 VK_* 常量；新增 `_safe_key()` 兜异常写 `dsh-mini-error.log`；
   新增 `dev\check-constants.py` 按类静态检查（老源码跑它就是 1 条命中：`DshGui.self.VK_ESCAPE`）。
2. **思考过程看不见**：`DEFAULT_CONFIG["show_reasoning"]` 是 false，而 GUI 的
   `GuiEmitter.on_reasoning` 受它控制（TUI 用的是另一份配置，所以那边看得见）。
   修复：默认 true + F4/菜单「显示思考过程」可切换（写进配置）+ 思考时状态栏
   提示"按 Esc 或点「停止」可打断"。真接口实测已能看到模型思维链。
3. **Esc 之外再加一个「停止」按钮**，运行中才可点；一轮结束后焦点自动回到输入框。
4. **设置流程简化**：删掉第三步"手输模型名"（会卡人的那一步），改成
   接口地址 → API Key → **自动接着弹出模型列表**（扫 `/models` + 可手输，取消=保持当前）；
   首次使用（没有 api_key）启动即自动弹出设置。
   顺带修：`OpenAICompletionsClient` 把 base_url/api_key 缓存在实例里，F2 保存后
   请求里带的还是旧值 —— 现在保存即 `client.reload()`。
5. **输出视图对齐下边缘**：三种写法实测对比（139 行文本、视口 28 行）：

   | 写法 | 首个可见行 | 结果 |
   |---|---|---|
   | 旧：整体重设 + `EM_SETSEL(-1,-1)` + `EM_SCROLLCARET` | 0 | 停在最上面 |
   | 中：插入点末尾 + `SB_BOTTOM` | 138 | 滚过头，末行顶到视口顶端、下面 27 行空白 |
   | 中：插入点末尾 + `EM_SCROLLCARET` | 111 | 差 6 行（SCROLLCARET 比实际视口保守） |
   | **新：只追加 + `EM_LINESCROLL(总行数-可见行数-首行)`** | 111 | ✓ 贴下边缘 |

   测试进程还必须 `SetProcessDPIAware()`，否则量到的客户区被虚化
   （574px 报成 459px），算出来的"可见行数"是错的。

验证（源码与打包产物各一遍）：

* `--selftest` **80/80 通过**（新增 7 项：快捷键常量齐全、快捷键异常留日志、
  `client.reload()` 立刻生效、GUI 显示思维链、思考时状态栏提示、关掉思维链不再输出、
  默认配置显示思维链）。
* `dev\gui-e2e.py`（内置假网关，不联网）**38/38 通过**：首次自动弹设置、两步标签与焦点、
  真模态、密钥打码、键盘输入落盘、自动进模型选择、列表来自 /models、改密钥立刻生效、
  思维链可见、长输出后视图贴底、Esc（焦点不在输入框时也管用）与「停止」都能真停住。
* `dev\real-api-test.py`（真实网关 + 真实模型）**8/8 通过**：
  正文与思维链都显示、思考中途 Esc 真能打断、输出贴下边缘。
* `--gui-autoclose 20` 冒烟退出码 0，诊断实测"持久会话可用 / 单命令模式可用"。

### v1.1.3（修「密钥填不进去」+ GUI 对话框加固）

用户反馈：按 F2 填 API Key 时打不进字，点确定也没反应。实测查出**三个叠在一起的缺陷**：

1. **焦点没给编辑框**：`GetGUIThreadInfo` 实测线程焦点是对话框窗口本身，
   不是里面的 EDIT → 键盘输入全丢。修复：弹出后 `SetActiveWindow` + `SetFocus(编辑框)`
   + `EM_SETSEL(0,-1)` 全选原值。
2. **窗口类重复注册**：每次弹框都拿同名类 `RegisterClassExW`（第二次起必然失败），
   新窗口继续用第一个窗口的过程（闭包）→ 第 2 个框标签还是第 1 个框的
   （实测"API Key"显示成"接口地址 base_url"），填的值写进已作废的返回值。
   修复：`gui_register_window_class()` 同名只注册一次 + 回调常驻，
   状态按 hwnd 存（`_states` / `_creating`）。
3. **消息循环不退出**：`GetMessage` 只在 WM_QUIT 时返回 0，
   `DestroyWindow(对话框)` 之后照样一直等消息 → `ask()` 永不返回、
   `_on_setup` 永远走不到 `save_config_file`。用 stderr 标记实测到
   `MARK setup-enter` 打了两次而 `MARK ask-returned` 一次都没有，
   即"F2 还能叠加第二个设置流程"。修复：`gui_run_modal()` 每轮检查
   `done`/`IsWindow(dlg)`，关窗时 `gui_wake_message_loop()`（PostThreadMessage
   WM_NULL）立刻叫醒循环；弹框期间 `EnableWindow(owner, False)` 做真模态。

顺带修掉：模型选择框漏了 `LBS_NOTIFY`（"双击列表里的模型"从来没生效）、
列表选中项会被预填的输入框内容盖掉（现在"手输了以手输为准，没手输用列表选中项"）；
诊断窗口重复打开会串状态（改为一次一个实例）；"另存诊断文件"的 CRCRLF；
`--config` 指定文件时 F2 保存写错位置。

验证：

* `--selftest` **73/73 通过**（新增 8 项：窗口类只注册一次、输入框焦点在编辑框上、
  预填值、密钥打码、非密钥不打码、确定后 ask 返回并带回值、取消返回空值、
  状态按 hwnd 隔离）。
* `--gui-autoclose 20` 冒烟：退出码 0；诊断窗口实测"持久会话可用 / 单命令模式可用"。
* **`dev\gui-e2e.py` 26/26 通过**（源码与 exe 各跑一遍）：三个输入框标签依次正确、
  焦点都在编辑框上、主窗被禁用（真模态）、密钥框打码且有"显示密钥"、
  `PostMessage(WM_CHAR)` 打进去的三个值**原样落进 dsh-mini.config.json**、
  第 1 步的回车能确认、日志出现"配置已保存"；模型选择框回车确认 +
  手输模型名生效。

### v1.1.2（缓存命中可见 + 自检判据修正）

* include_usage 默认开启，format_usage() 显示 缓存命中/未命中 token 数（TUI 与 GUI 都显示）；
  _refresh_system_prompt() 内容不变不再重新赋值，保证前缀缓存命中。
* 自检判据不再假设 PowerShell 报错是英文（中文系统输出的是本地化文案）。
* --selftest 65/65 通过（windowed，经诊断窗口输出）。


### v1.0.3（图形界面版）

* `dsh-mini-gui.exe` 冒烟测试（`--gui-autoclose 20`）：
  * 窗口确实创建且可见 —— Win32 `EnumWindows` 实测到
    `class='DshMiniGuiWnd' visible=True title='DSH 极简 Agent v1.0.3 —— 图形界面（无控制台）'`；
  * 无控制台黑框（`console=False` 打包；子 PowerShell 走 `CREATE_NO_WINDOW`）；
  * 自动跑完整 `--shellcheck`：持久会话 4.8s 通过、单命令模式 2.0s 通过；
  * 全部输出落进 `dsh-mini-gui.log`（UTF-8 中文完整），进程退出码 0。
* ctypes 回调签名问题已修（未声明 argtypes 时 64 位 `LPARAM` 会被截断成 32 位，
  表现为 `OverflowError: int too long to convert` 与 `CallWindowProc` 访问违例）。
* 命令行版 `dsh-mini.exe --selftest`：**65/65 通过**；`--version` 输出 `1.0.3`。
* 两个 exe 版本资源均为 1.0.3.0，UCRT / vcruntime140 / python38 全部内嵌。

### v1.0.2（Win7 控制台编码崩溃修复）

* **复现**：把 `sys.stdout` 换成 cp936（严格）流后打印 `»`，稳定抛
  `UnicodeEncodeError: 'gbk' codec can't encode character '\xbb'`。
  这正是 Win7 上「一用命令就崩」的现场（栈顶是打印用的 lambda）。
  本机 Win10 控制台走 Unicode 接口（`chcp 936` 下 `sys.stdout.encoding` 仍是 `utf-8`），
  所以正常路径永远不复现 —— 与「只在 Win7 出问题」完全吻合。
* **修复后**：同样的 cp936 严格流下，`»` `›` `…` `│` `●` 与中文混排全部正常写出，
  `»` `›` 自动降级为 `>`；GBK 里存在的符号（`√` `×` `│` `●`）保持原样。
* `dsh-mini.exe --selftest`：**65/65 通过**（新增 6 项：
  `console_safe` 降级、`console_safe` 保留 GBK 字符、U+FFFD 处理、
  `SafeStream` 写 cp936 严格流不崩、降级结果正确、崩溃日志可写出）。
* 崩溃自留证据：`write_crash_log` 实测写出 `dsh-mini-error.log`，
  含系统版本、Python 版本、`stdout`/`stderr` 的 encoding/errors/isatty、完整堆栈。
* `chcp 936` 控制台下跑 `dsh-mini.exe --selftest` 与 `--shellcheck`：全部通过，无崩溃日志。
* 打包产物：版本资源 1.0.2.0，UCRT / vcruntime140 / python38 仍全部内嵌。

### v1.0.1（Win7 命令执行修复）

* `dsh-mini.exe --selftest`：**59/59 通过**。除原有编辑器/编码/配置项外，新增：
  * 单命令模式可用、输出无 CLIXML 垃圾、退出码透传（`cmd /c exit 5` → 5）、错误信息可读；
  * 持久会话握手失败时**自动降级**且不卡死（含"握手超时"这一路，单独实测过）；
  * 候选 PowerShell 枚举；
  * 协议守卫：不带 base64 解码、不依赖 `-Command -`。
* `dsh-mini.exe --shellcheck`：本机（Win10 + PS 5.1）持久会话与单命令模式均实测通过。
* 独立协议矩阵（`pwsh` 工具层，Windows PowerShell 5.1 实测，持久 / 单命令两种模式各跑一遍）：
  * 中文输出、多行脚本、CRLF 多行、单双引号混合、`@` 与分号、超长命令（临时 .ps1）全部正确；
  * `$global:` 变量跨调用保持（持久模式）、工作目录跨调用保持（持久模式）；
  * 非零退出码、终止性错误（`throw`）、原生命令报错文本全部如实回传，且**不含 CLIXML**；
  * `shell_exe` 指错 → 明确提示并改用可用项；无 PowerShell → 快速报错，不再无声卡死。
* 真实接口闭环（本地网关 + 真实模型）：`-p` 单次执行触发 `pwsh` 工具，
  3.3 秒返回命令实际输出 `win7-command-fix-ok 13:21:08`。

### v1.0.0（初版）

* `--selftest` 35/35 通过。
* 真实接口闭环（某 OpenAI 兼容网关 + 通用对话模型）：
  一次提问内连续调用 4 次工具（2×`pwsh` + 2×`str_replace_editor`）全部成功；
  `$global:live=2026` 跨 `pwsh` 调用保持；中文输出正确回传。
* 工作区内文件操作闭环（真实接口，一次提问内 5 次工具调用），磁盘结果与预期一致。
* 首次运行配置向导（stdin 重定向场景）正常。

## 七、杀软误报说明

PyInstaller 打包的单文件 exe 偶尔会被 Windows Defender 之类的杀软按启发式判成
`Trojan:Win32/Bearfoos.A!ml` 这类误报（v1.0.1 构建过程中在本机出现过一次，
该判定是机器学习启发式，不是真的报毒）。已经做了这些降低误报的处理：

* 协议不使用 base64 解码 + `Invoke-Expression`（无文件执行的经典特征），
  改为 UTF-8 纯文本 + `\x1e` 换行占位；
* 超长命令走临时 `.ps1` + `-File`（正常机制），不再读文件后动态执行；
* exe 写入正规的版本资源（右键属性可见版本/产品名）。

如果仍然被拦：把 exe 所在目录加入杀软白名单，或用 `dev\build-win7-exe.bat`
在本机从源码重新打包（源码全在 `dev\dsh-mini.py`，一眼可查，无任何后门）。

## 八、已知限制

* 只实现 OpenAI Chat Completions；不支持 Responses API、Anthropic Messages 等格式。
* `pwsh` 不支持交互式输入（等待 stdin 的命令会超时）。
* 「单命令模式」（降级后或手动指定时）不保持变量与当前目录，
  这是老系统上保证"命令一定能跑"的代价；`describe()` 会把这一点写进系统提示词，
  模型据此会主动避免依赖跨调用状态。
* 单命令模式没有实时输出（结果一次性返回）；持久模式有逐行实时输出。
* 超长单条命令（> 8000 字符）在单命令模式下会落一个临时 `.ps1` 到 `%TEMP%`，
  正常结束后自动删除。
* Win7 的 conhost 不支持 ANSI 转义，颜色自动关闭，光标定位改用 Win32 API；
  若输入法或字体导致行编辑异常，可用 `--editor line` 切换为简单输入模式。
* 上下文不会自动压缩（与 DSH 极简模式一致），超长对话请用 `/clear` 或 `/resume`。
