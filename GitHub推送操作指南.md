# GitHub 代码推送操作指南（AI 助手与开发者通用）

> **安全红线**：
> 1. 本项目代码库及所有配置文件中**严禁硬编码或持久化保存 GitHub 令牌（Personal Access Token）**。
> 2. 每次需要向 GitHub 推送代码时，**AI 助手必须在对话中向用户索取一次性令牌**。
> 3. 推送完成后，必须立刻将远程仓库 URL 恢复为不含令牌的纯净地址。

---

## 一、当前项目配置信息

* **项目名称**：mini-agent（DSH 极简模式 Agent 工具）
* **GitHub 仓库地址**：`https://github.com/lhy302/mini-agent.git`
* **默认主分支**：`main`
* **本地工作区路径**：`D:\my-project\工作区1`
* **Git 可执行程序路径**：`C:\Program Files\Git\cmd\git.exe`

---

## 二、推送前环境前置检查

在执行推送前，必须确认以下两项已就绪：

1. **网络加速正常开启**：
   * 确保 `Watt Toolkit`（桌面快捷方式）已开启 GitHub 加速。
   * Git 已配置跳过本地反向代理证书验证：
     ```powershell
     & "C:\Program Files\Git\cmd\git.exe" config --system http.sslVerify false
     ```
2. **敏感与日志文件已过滤**：
   * 确认 `.gitignore` 包含 `*.log`、`dsh-mini-gui.log`、`**/pwsh/` 等临时或大体积运行时文件。

---

## 三、标准推送流程（后续 AI 助手执行规范）

当用户发出“帮我推送更新到 GitHub”或类似指令时，按以下四步执行：

### 第一步：检查工作区变动并暂存
```powershell
Set-Location "D:\my-project\工作区1"
$git = "C:\Program Files\Git\cmd\git.exe"

# 查看修改的文件
& $git status

# 暂存所有有效修改
& $git add .
```

### 第二步：创建清晰规范的提交信息 (Commit)
```powershell
& $git commit -m "feat/fix/docs: 简要描述本次改动内容"
```

### 第三步：向用户索取令牌并执行推送
向用户发送提示：
> “请提供你的 GitHub Personal Access Token（令牌），我将立即为你推送代码。”

收到令牌（形如 `ghp_xxxxxxxxxxxxxxxxxxxx`）后，在 PowerShell 中执行无明文存留的推送：
```powershell
$token = "<用户提供的令牌>"
$pushUrl = "https://$token@github.com/lhy302/mini-agent.git"

# 执行推送到远程 main 分支
& $git push $pushUrl main
```

### 第四步：立刻清除远程地址中的令牌
推送完成后，必须立刻执行以下命令，确保本地 git 配置中只留下纯净地址：
```powershell
& $git remote set-url origin https://github.com/lhy302/mini-agent.git
& $git remote -v
```

---

## 四、大文件与发布建议

1. **单文件大小限制**：
   * GitHub 单个文件绝对上限为 **100 MB**。
   * 单文件超过 50 MB 时，GitHub 会产生警告提示，但仍允许推送到仓库。
2. **可执行文件分发建议**：
   * 封装的独立 EXE 程序已直接打包纳入仓库。
   * 后续若有更新版本，可直接利用 GitHub 的 **Releases（版本发布）** 功能上传大安装包，方便用户下载。
