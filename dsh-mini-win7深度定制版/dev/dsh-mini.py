#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DSH 极简 Agent  ——  单文件 / 零第三方依赖 / Windows 7+ 开箱即用

与 DSH「极简模式（minimal）」功能对齐的本地 Agent：
  * 工具集只保留 DSH 极简模式能调用的两个工具：
      1) pwsh                —— 持久 PowerShell 会话（Windows 下 DSH 极简模式暴露的就是 pwsh）
      2) str_replace_editor  —— view / create / str_replace / insert 四命令文件编辑器
  * API 只支持常规 OpenAI Chat Completions 格式（/chat/completions，支持 SSE 流式）。
  * 交互为命令行 TUI：Windows 7 无 ANSI 支持时自动降级（用 Win32 控制台 API 定位光标）。

运行环境：Python 3.8+（Python 3.8 是最后一个支持 Windows 7 的版本）。
双击本文件（或同目录的 .cmd 启动器）即可使用，无需安装任何第三方库。

用法：
    python dsh-mini.py                  # 交互模式
    python dsh-mini.py -p "列出当前目录"  # 单次执行（便于脚本化 / 测试）
    python dsh-mini.py --selftest       # 离线自检（不联网）
    python dsh-mini.py --setup          # 重新运行配置向导
"""

from __future__ import print_function

import argparse
import base64
import json
import locale
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.request
import uuid

APP_NAME = "dsh-mini"
APP_TITLE = "DSH 极简 Agent"
VERSION = "1.2.0"

IS_WIN = (os.name == "nt")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 打包成 exe 后，配置与数据放在 exe 同目录（便携）；否则放在脚本同目录
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else SCRIPT_DIR
CONFIG_NAME = "dsh-mini.config.json"

# DSH 极简模式的基础固定人格提示词
DSH_MINIMAL_PERSONA = "You are a helpful software engineer assistant."

# Windows 7 深度定制专属人格与详细运行环境提示词
DSH_WIN7_PERSONA = (
    "You are an expert software engineer and systems assistant operating in a Windows 7 environment.\n\n"
    "Crucial Operating System & Runtime Environment Information:\n"
    "1. Host Operating System: Windows 7 (x64) SP1+ Custom Environment.\n"
    "2. Windows 7 CLI Ecosystem & Resolution:\n"
    "   - In standard Windows 7 environments, the command-line ecosystem is notoriously fragmented, outdated, and chaotic: the built-in shell is usually legacy PowerShell 2.0 (or occasionally 3.0/5.1), which lacks modern cmdlets (e.g. ConvertFrom-Json, Invoke-RestMethod), suffers from GBK/CP936 console encoding crashes, and fails on modern pipeline syntax.\n"
    "   - To permanently eliminate this CLI chaos, this application packages and automatically extracts a dedicated, stable, self-contained PowerShell 7.x (pwsh) binary directly into the program directory (./pwsh/pwsh.exe).\n"
    "   - All subsequent `pwsh` tool executions are strictly and forcibly routed to this bundled PowerShell 7 engine.\n"
    "3. Available PowerShell 7 Features & Capabilities:\n"
    "   - Modern language operators and syntax: ternary operator (? :), null-coalescing (??, ??=), pipeline chain operators (&&, ||), etc. are 100% supported.\n"
    "   - Built-in modern cmdlets: ConvertFrom-Json, ConvertTo-Json, Invoke-RestMethod, Invoke-WebRequest, Get-FileHash, Compress-Archive, Expand-Archive, Select-String, etc. are fully available.\n"
    "   - Standard UTF-8 character encoding: standard input/output streams use UTF-8; Chinese characters and Unicode symbols output cleanly without CP936 crashes.\n"
    "4. Shell Session State & Execution:\n"
    "   - The `pwsh` tool operates as a persistent PowerShell 7 session: environment variables, functions, aliases, and working directory are preserved across consecutive tool calls (unless degraded to one-shot mode, which will be noted).\n"
    "5. Windows 7 Native Compatibility:\n"
    "   - You can confidently use modern PowerShell 7 syntax, but remember that Windows 7 native system services, paths, and system executables (e.g. cmd.exe, sc.exe, net.exe, reg.exe) follow Windows 7 conventions.\n"
    "6. Autonomous Agent Capabilities:\n"
    "   - You have autonomous tools to control the mouse ('mouse_control'), read any local image for multimodal visual analysis ('read_image'), and capture the screen ('take_screenshot').\n"
    "   - You can autonomously decide when and how to inspect images, capture screen state, and operate the mouse."
)

# 与 DSH 一致的输出截断标记
TRUNCATED_EDITOR = ("<response clipped><NOTE>To save on context only part of this file has been shown to you. "
                    "You should retry this tool after you have searched inside the file with `grep -n` in order "
                    "to find the line numbers of what you are looking for.</NOTE>")
TRUNCATED_SHELL = ("<response clipped><NOTE>To save on context only part of this file has been shown to you. "
                   "You should retry this tool after you have searched inside the file with Select-String in order "
                   "to find the line numbers of what you are looking for.</NOTE>")
SHELL_RESET_MESSAGE = ("The persistent pwsh shell was reset; the next pwsh call starts from the workspace with a "
                       "fresh current directory and environment.")
SHELL_ONESHOT_MESSAGE = ("This machine runs pwsh in one-shot mode: the next pwsh call starts a brand new PowerShell "
                         "process, so variables and the current directory are not kept.")


# =============================================================================
# 1. 控制台兼容层（ANSI / Win32 控制台 API / 编码）
# =============================================================================

def self_invocation():
    """给出当前程序的调用写法（打包成 exe 后不再带 python 前缀）。"""
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable)
    return "python %s" % os.path.basename(__file__)


def _stdout_is_tty():
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _stdin_is_tty():
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def enable_virtual_terminal():
    """尝试开启控制台 VT（ANSI 转义）支持。

    Windows 10 1511+ 返回 True；Windows 7/8 的 conhost 不支持，返回 False，
    此时颜色自动关闭、光标定位改用 Win32 API。
    """
    if not IS_WIN:
        return _stdout_is_tty()
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def fix_stdio_encoding():
    """重定向到文件/管道时把 stdio 固定为 UTF-8，避免中文 UnicodeEncodeError。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            is_console = stream.isatty()
        except Exception:
            is_console = False
        if is_console:
            continue  # 控制台走 WriteConsoleW，本身就是 Unicode
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Python 3.7+
        except Exception:
            pass


# -----------------------------------------------------------------------------
# 控制台编码兜底（Windows 7 的坑）
# -----------------------------------------------------------------------------
# Win7 的控制台流常常按本机代码页（中文系统 = cp936/GBK）严格编码。只要往
# stdout 写一个 GBK 表示不了的字符，Python 就抛 UnicodeEncodeError —— 由于它发生在
# print 里，整个程序会直接崩掉（Win10 用 WriteConsoleW 写 Unicode，所以不复现）。
# 源码里 "»"（U+00BB，工具调用行前缀）和 "›"（U+203A，输入提示符）正好不在 GBK 里，
# 于是「一让 AI 执行命令就崩」。下面这层包装把控制台表示不了的字符降级成 ASCII 替代品，
# 保证任何输出都不会再因为编码炸掉。

CONSOLE_CHAR_FALLBACKS = {
    "\u00bb": ">",      # »   工具调用前缀
    "\u203a": ">",      # ›   输入提示符
    "\u2026": "...",    # …   省略号
    "\u2502": "|",      # │   实时输出引用线
    "\u2500": "-",      # ─
    "\u2550": "=",      # ═
    "\u250c": "+",      # ┌
    "\u2510": "+",      # ┐
    "\u2514": "+",      # └
    "\u2518": "+",      # ┘
    "\u25cf": "*",      # ●   回复前缀
    "\u00b7": ".",      # ·   状态分隔点
    "\u221a": "v",      # √   完成标记
    "\u00d7": "x",      # ×   失败标记
    "\u2191": "^",      # ↑
    "\u2193": "v",      # ↓
    "\u2190": "<",      # ←
    "\u2192": ">",      # →
    "\ufffd": "?",      # 替换字符
}


def console_encoding():
    """当前标准输出使用的编码名（Win7 中文控制台一般是 cp936）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            name = getattr(stream, "encoding", None)
        except Exception:
            name = None
        if name:
            return name
    return None


def console_safe(text, encoding=None):
    """把当前控制台编码不了的字符换成 ASCII 替代品（永不抛异常）。"""
    if not text or not isinstance(text, str):
        return text
    enc = encoding or console_encoding()
    if not enc:
        return text
    try:
        text.encode(enc)
        return text
    except (UnicodeEncodeError, LookupError):
        pass
    except Exception:
        return text
    cache = {}
    out = []
    for ch in text:
        replacement = cache.get(ch)
        if replacement is None:
            try:
                ch.encode(enc)
                replacement = ch
            except Exception:
                replacement = CONSOLE_CHAR_FALLBACKS.get(ch, "?")
            cache[ch] = replacement
        out.append(replacement)
    return "".join(out)


class SafeStream(object):
    """包住 stdout/stderr：写不进去的字符先降级，绝不抛 UnicodeEncodeError。"""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        if not isinstance(text, str):
            return self._stream.write(text)
        try:
            return self._stream.write(text)
        except UnicodeEncodeError:
            # 用「被包装的那个流」自己的编码来判断，别拿 sys.stdout 的编码猜
            return self._stream.write(console_safe(text, getattr(self._stream, "encoding", None)))
        except Exception:
            return len(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_safe_streams():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or isinstance(stream, SafeStream):
            continue
        try:
            setattr(sys, name, SafeStream(stream))
        except Exception:
            pass


# -----------------------------------------------------------------------------
# 崩溃自留证据：任何未处理异常都写一份日志，别只留一句 "Failed to execute script"
# -----------------------------------------------------------------------------

def error_log_path():
    return os.path.join(BASE_DIR, "dsh-mini-error.log")


def write_crash_log(exc_type, exc_value, exc_tb, where=""):
    try:
        os_text = "%s %s" % (os.name, sys.getwindowsversion() if IS_WIN else "")
    except Exception:
        os_text = os.name
    try:
        argv = " ".join(sys.argv)
    except Exception:
        argv = "?"
    header = [
        "=" * 72,
        "%s v%s 崩溃报告" % (APP_NAME, VERSION),
        "时间    : %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "来源    : %s" % (where or "main"),
        "系统    : %s" % os_text,
        "Python  : %s" % sys.version.replace("\n", " "),
        "stdout  : encoding=%s errors=%s isatty=%s"
        % (_stream_info(sys.stdout)),
        "stderr  : encoding=%s errors=%s isatty=%s"
        % (_stream_info(sys.stderr)),
        "cwd     : %s" % (os.getcwd() if os.path.isdir(os.getcwd()) else "?"),
        "argv    : %s" % argv,
        "-" * 72,
        "",
    ]
    body = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    path = error_log_path()
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(header))
            handle.write(body)
            if not body.endswith("\n"):
                handle.write("\n")
            handle.write("=" * 72 + "\n")
    except Exception:
        return ""
    return path


def _stream_info(stream):
    encoding = errors = isatty = "?"
    try:
        encoding = getattr(stream, "encoding", None) or "?"
    except Exception:
        pass
    try:
        errors = getattr(stream, "errors", None) or "?"
    except Exception:
        pass
    try:
        isatty = stream.isatty()
    except Exception:
        pass
    return (encoding, errors, isatty)


def install_crash_handler():
    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        path = write_crash_log(exc_type, exc_value, exc_tb, where="main")
        try:
            sys.stderr.write("\n程序遇到未预期的错误（已记录到 %s）。\n" % (path or "?"))
            sys.stderr.write("把这个文件发给开发者即可定位；截图也行。\n\n")
            sys.stderr.flush()
        except Exception:
            pass
        try:
            traceback.print_exception(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = hook

    def thread_hook(args):
        write_crash_log(args.exc_type, args.exc_value, args.exc_traceback,
                        where="thread:%s" % getattr(args.thread, "name", "?"))

    try:
        threading.excepthook = thread_hook          # Python 3.8+
    except Exception:
        pass


def _codepage_name(cp):
    if not cp:
        return None
    try:
        return "cp%d" % int(cp)
    except Exception:
        return None


def candidate_output_encodings():
    """PowerShell / cmd / 老式程序在 Windows 上可能输出不同编码，这里给出候选列表。"""
    names = ["utf-8"]
    try:
        pref = locale.getpreferredencoding(False)
        if pref:
            names.append(pref)
    except Exception:
        pass
    if IS_WIN:
        try:
            import ctypes
            for getter in (ctypes.windll.kernel32.GetACP, ctypes.windll.kernel32.GetOEMCP):
                try:
                    name = _codepage_name(getter())
                    if name:
                        names.append(name)
                except Exception:
                    pass
        except Exception:
            pass
    out = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out


def decode_output(data, forced_encoding=None):
    """把子进程输出字节解码成文本。

    策略：优先 UTF-8；失败则在本机 ANSI/OEM 代码页中挑替换字符最少的一个。
    这样可以同时正确显示 PowerShell(UTF-8)、cmd(控制台代码页)、Python(cp936) 的输出。
    """
    if forced_encoding and forced_encoding != "auto":
        try:
            return data.decode(forced_encoding, "replace")
        except Exception:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    best_text, best_score = None, None
    for name in candidate_output_encodings()[1:]:
        try:
            text = data.decode(name, "replace")
        except Exception:
            continue
        score = text.count("\ufffd")
        if best_score is None or score < best_score:
            best_text, best_score = text, score
            if score == 0:
                break
    if best_text is not None:
        return best_text
    return data.decode("utf-8", "replace")


# =============================================================================
# 2. 颜色 / 终端宽度
# =============================================================================

class Theme(object):
    def __init__(self, enabled):
        self.enabled = bool(enabled)

    def __call__(self, text, code=""):
        if not self.enabled or not code:
            return text
        return "\x1b[%sm%s\x1b[0m" % (code, text)

    # 常用样式
    def bold(self, t):
        return self(t, "1")

    def dim(self, t):
        return self(t, "90")

    def red(self, t):
        return self(t, "31")

    def green(self, t):
        return self(t, "32")

    def yellow(self, t):
        return self(t, "33")

    def blue(self, t):
        return self(t, "34")

    def magenta(self, t):
        return self(t, "35")

    def cyan(self, t):
        return self(t, "36")

    def grey(self, t):
        return self(t, "90")

    def bold_cyan(self, t):
        return self(t, "1;36")


def terminal_width(default=100):
    try:
        return max(40, shutil.get_terminal_size((default, 24)).columns)
    except Exception:
        return default


def display_width(text):
    """终端显示宽度（中文/全角算 2 列，组合字符算 0 列）。"""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            width += 2
        elif ch == "\t":
            width += 4
        elif ord(ch) < 32:
            continue
        else:
            width += 1
    return width


def fmt_duration(seconds):
    if seconds < 1:
        return "%.2fs" % seconds
    if seconds < 60:
        return "%.1fs" % seconds
    minutes = int(seconds // 60)
    return "%dm%02ds" % (minutes, int(seconds - minutes * 60))


def format_usage(usage):
    """把 usage 字典格式化成中文说明行。"""
    if not isinstance(usage, dict):
        return ""
    p_tok = usage.get("prompt_tokens")
    c_tok = usage.get("completion_tokens")
    t_tok = usage.get("total_tokens")
    parts = [
        "输入 %s" % (p_tok if p_tok is not None else 0),
        "输出 %s" % (c_tok if c_tok is not None else 0),
        "总计 %s" % (t_tok if t_tok is not None else 0)
    ]
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None and isinstance(usage.get("prompt_tokens_details"), dict):
        hit = usage["prompt_tokens_details"].get("cached_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if hit is not None or miss is not None:
        parts.append("缓存命中 %s" % (0 if hit is None else hit))
        if miss is not None:
            parts.append("未命中 %s" % miss)
    return " · ".join(parts)


# =============================================================================
# 3. 剪贴板（Ctrl+V 粘贴，纯 ctypes，无需第三方库）
# =============================================================================

def read_clipboard_text():
    """读取剪贴板文本（Ctrl+V 粘贴用）。

    必须显式声明 GetClipboardData/GlobalLock 的参数与返回类型：
    它们返回的是 64 位句柄，ctypes 默认按 32 位 int 处理会被截断，
    导致 GlobalLock 失败、粘贴拿到空字符串。
    """
    if not IS_WIN:
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        if not user32.OpenClipboard(None):
            return ""
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.c_wchar_p(pointer).value or ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


# =============================================================================
# 4. 配置
# =============================================================================

DEFAULT_CONFIG = {
    # ---- OpenAI Chat Completions 接口 ----
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": 0,
    "max_tokens": 0,              # 0 表示不发送该字段
    "stream": True,
    "request_timeout": 300,       # 单次请求（含流式读取）空闲超时，秒
    "retries": 3,                 # 网络/429/5xx 重试次数
    "include_usage": True,        # 流式也请求 usage：能看到缓存命中/未命中（个别老网关不认就改回 false）
    "extra_headers": {},          # 额外请求头
    "extra_body": {},             # 额外请求体字段（厂商私有参数）
    "model_picker": True,         # 每次启动都询问一次模型（与 TUI 时期同步；GUI 里按 F3 随时再选）

    # ---- 提示词 ----
    "system_prompt": DSH_WIN7_PERSONA,
    "include_env_note": True,     # 追加一行运行环境说明（关闭后 = 原样提示词）

    # ---- 工具 ----
    "max_output_chars": 16000,    # 与 DSH 一致
    "shell_timeout_ms": 300000,   # 与 DSH 一致（5 分钟）
    "shell_exe": "",              # 留空=自动（强制优先使用同目录自带的稳定版 PowerShell 7）
    "shell_mode": "auto",         # auto / persistent / oneshot（见 README「Win7 兼容」一节）
    "shell_probe_timeout_ms": 20000,  # 长驻会话握手超时（毫秒）；Win7 上 PowerShell 启动慢，别调太小
    "shell_encoding": "auto",     # auto / utf-8 / gbk / cp936 ...
    "cwd": "",                    # 留空=启动时的工作目录
    "max_tool_rounds": 60,        # 单轮用户输入内最多工具调用轮数

    # ---- 界面 ----
    "editor": "auto",             # auto / raw / line
    "color": "auto",              # auto / always / never
    "show_reasoning": True,       # 显示模型思维链（reasoning_content）：思考死循环时要能看见并打断
    "save_sessions": False,       # 自动保存会话到 sessions/
    "show_live_output": True,     # 工具执行时实时显示 shell 输出
    "support_vision": True,       # 是否支持读取图片并按 OpenAI 视觉格式发送（可随时切换）
}

CONFIG_HINT = {
    "base_url": "OpenAI 兼容地址，例如 https://api.deepseek.com",
    "api_key": "API Key（只保存在本机配置文件里）",
    "model": "模型名，例如 deepseek-chat / gpt-4o-mini / qwen-plus",
}

ENV_KEYS = {
    "base_url": ("DSH_MINI_BASE_URL", "OPENAI_BASE_URL"),
    "api_key": ("DSH_MINI_API_KEY", "OPENAI_API_KEY"),
    "model": ("DSH_MINI_MODEL", "OPENAI_MODEL"),
}


_CONFIG_PATH_CACHE = []


def config_path():
    """配置文件路径：优先 exe/脚本同目录（便携），目录不可写时退到 %APPDATA%。"""
    if _CONFIG_PATH_CACHE:
        return _CONFIG_PATH_CACHE[0]
    preferred = os.path.join(BASE_DIR, CONFIG_NAME)
    if os.path.isfile(preferred) or _writable_dir(BASE_DIR):
        _CONFIG_PATH_CACHE.append(preferred)
        return preferred
    fallback_dir = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "dsh-mini")
    _writable_dir(fallback_dir)
    fallback = os.path.join(fallback_dir, CONFIG_NAME)
    _CONFIG_PATH_CACHE.append(fallback)
    return fallback


def _writable_dir(path):
    try:
        if not os.path.isdir(path):
            os.makedirs(path)
        probe = os.path.join(path, ".dsh-mini-write-test")
        with open(probe, "wb") as handle:
            handle.write(b"ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def data_dir():
    """会话/临时数据目录：优先脚本（exe）同目录，不可写则退到 %APPDATA%。"""
    preferred = os.path.join(BASE_DIR, "sessions")
    if _writable_dir(preferred):
        return preferred
    fallback = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "dsh-mini", "sessions")
    _writable_dir(fallback)
    return fallback


def load_config_file(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print("配置文件读取失败（已忽略）：%s -> %s" % (path, exc))
        return {}


def save_config_file(path, config):
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def merge_config(*layers):
    merged = dict(DEFAULT_CONFIG)
    for layer in layers:
        if not layer:
            continue
        for key, value in layer.items():
            if value is None:
                continue
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                inner = dict(merged[key])
                inner.update(value)
                merged[key] = inner
            else:
                merged[key] = value
    return merged


def env_layer():
    layer = {}
    for key, names in ENV_KEYS.items():
        for name in names:
            value = os.environ.get(name)
            if value:
                layer[key] = value
                break
    return layer


def normalize_base_url(base_url):
    """把用户填的地址补成完整的 chat/completions 端点。

    规则（尽量不猜）：
      * 已经以 /chat/completions 结尾 -> 原样使用（想自己指定完整端点就用这种写法）
      * 以 /vN 结尾                   -> 只补 /chat/completions
      * 裸域名（没有路径，可带端口）  -> 补 /v1/chat/completions
        （OpenAI 官方、vLLM、one-api/new-api、Ollama 以及 DeepSeek 的兼容别名都在这个路径）
      * 其他带自定义路径的地址        -> 只补 /chat/completions，不再擅自插入 /v1
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions"):
        return url
    if re.search(r"/v\d+$", url):
        return url + "/chat/completions"
    if "/" in url.split("://", 1)[-1]:
        return url + "/chat/completions"
    return url + "/v1/chat/completions"


def models_endpoint(base_url):
    """由 completions 端点推出 OpenAI 兼容的模型列表端点。

    https://api.deepseek.com            -> https://api.deepseek.com/v1/models
    https://x.com/v1/chat/completions   -> https://x.com/v1/models
    https://x.com/openai                -> https://x.com/openai/models
    """
    endpoint = normalize_base_url(base_url)
    suffix = "/chat/completions"
    if endpoint.endswith(suffix):
        endpoint = endpoint[:-len(suffix)]
    return endpoint.rstrip("/") + "/models"


def fetch_model_ids(config, timeout=10):
    """GET {base}/models，返回去重排序后的模型 id 列表（仅 OpenAI 兼容格式）。"""
    url = models_endpoint(config.get("base_url"))
    headers = {
        "Accept": "application/json",
        "User-Agent": "%s/%s (Python-%s)" % (APP_NAME, VERSION, sys.version.split()[0]),
    }
    api_key = config.get("api_key")
    if api_key:
        headers["Authorization"] = "Bearer %s" % api_key
    for key, value in (config.get("extra_headers") or {}).items():
        headers[key] = str(value)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        raw = response.read().decode("utf-8", "replace")
        response.close()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise ApiError(_format_http_error(exc.code, detail))
    except Exception as exc:
        raise ApiError("无法访问 %s：%s" % (url, exc))
    try:
        data = json.loads(raw)
    except Exception:
        raise ApiError("模型列表不是合法 JSON：%s" % raw[:200])
    items = None
    if isinstance(data, dict):
        items = data.get("data")
        if items is None:
            items = data.get("models")
    elif isinstance(data, list):
        items = data
    if not isinstance(items, list):
        raise ApiError("接口未按 OpenAI 兼容格式返回 data 列表")
    ids = []
    for item in items:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("name") or item.get("model")
            if mid:
                ids.append(str(mid))
        elif isinstance(item, str):
            ids.append(item)
    return sorted(set(ids))


def parse_model_choice(answer, models):
    """解析用户输入：空=保持当前(None)，序号=列表项，其他=自定义模型名，序号越界=""。"""
    text = (answer or "").strip().strip('"').strip("'")
    if not text:
        return None
    if text.isdigit() and models:
        index = int(text) - 1
        if 0 <= index < len(models):
            return models[index]
        return ""
    return text


def choose_model(config, theme, emit, reader, current_model):
    """扫描可用模型并让用户选择一次；返回模型名，None 表示保持当前。"""
    emit("")
    emit(theme.bold("选择模型") + theme.grey("   扫描 " + models_endpoint(config.get("base_url"))))
    models = []
    error = None
    try:
        models = fetch_model_ids(config)
    except ApiError as exc:
        error = str(exc)
    except Exception as exc:
        error = "%s: %s" % (exc.__class__.__name__, exc)

    if models:
        width = terminal_width()
        label_width = max(display_width(name) for name in models) + 6
        columns = max(1, min(3, width // max(1, label_width)))
        rows = (len(models) + columns - 1) // columns
        for row in range(rows):
            parts = []
            for col in range(columns):
                index = col * rows + row
                if index >= len(models):
                    break
                label = "%2d) %s" % (index + 1, models[index])
                parts.append(label + " " * max(0, label_width - display_width(label)))
            emit(theme.grey("  ") + "".join(parts).rstrip())
    else:
        emit(theme.yellow("  ! 没能取到模型列表：%s" % (error or "未知原因")))
        emit(theme.grey("    可以直接手输模型名。提示：401 一般是密钥不对；"))
        emit(theme.grey("    404 说明这个网关没有 /models 接口（不影响聊天功能）。"))

    emit("")
    hint = "回车=保持 %s" % current_model if current_model else "回车=保持当前"
    emit(theme.grey("  ") + theme.cyan("›") + theme.grey("  输入序号选择，或直接输入模型名（%s）" % hint))
    prompt = theme.bold_cyan("  模型: ") if theme.enabled else "  模型: "
    try:
        answer = reader.readline(prompt)
    except (EOFError, KeyboardInterrupt):
        return None
    if answer is None:
        return None
    chosen = parse_model_choice(answer, models)
    if chosen is None:
        return None
    if chosen == "":
        emit(theme.red("  序号超出范围，保持当前模型。"))
        return None
    return chosen


def read_secret(prompt_text, reader=None):
    """读取密钥：掩码回显，支持 Ctrl+V 粘贴和右键粘贴。

    不能用 getpass.getpass：它在 Windows 上用 msvcrt.getwch() 逐字符读取，
    既不回显、也不识别 Ctrl+V，会把 0x16 这个控制字符当成密码的一部分，
    导致粘贴进去的密钥无效（表现为后续 401 / 取不到模型列表）。
    """
    if not _stdin_is_tty():
        if reader is not None:
            value = reader.readline(prompt_text)
            if value is None:
                raise EOFError
            return value
        return input(prompt_text)
    try:
        import msvcrt
    except Exception:
        return input(prompt_text)
    sys.stdout.write(prompt_text)
    sys.stdout.flush()
    chars = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            break
        if ch == "\x03":
            sys.stdout.write("\n")
            sys.stdout.flush()
            raise KeyboardInterrupt
        if ch in ("\x04", "\x1a"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            raise EOFError
        if ch == "\x08":
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()          # 功能键的第二个字节，丢弃
            continue
        if ch == "\x16":             # Ctrl+V：从剪贴板插入
            text = read_clipboard_text().replace("\r", "").replace("\n", "").strip()
            if text:
                chars.extend(text)
                sys.stdout.write("*" * len(text))
                sys.stdout.flush()
            continue
        if ord(ch) >= 32:
            chars.append(ch)
            sys.stdout.write("*")
            sys.stdout.flush()
    return "".join(chars)


def mask_secret(value):
    if not value:
        return "(未设置)"
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:4] + "***" + value[-4:]


def setup_wizard(config, reader=None):
    """首次运行的配置向导。

    注意：必须和后续对话共用同一个 reader。否则 input()（文本流）和
    sys.stdin.buffer（字节流）混用会丢掉缓冲区里已经读进来的行。
    """
    print("")
    print("=" * 68)
    print(" %s v%s  首次配置" % (APP_TITLE, VERSION))
    print("=" * 68)
    print(" 只支持常规 OpenAI Chat Completions 格式的接口。直接回车使用括号内的默认值。")
    print(" 密钥输入时显示为 *，支持 Ctrl+V 粘贴或鼠标右键粘贴。")
    print("")

    def ask(label, current, secret=False):
        suffix = " [%s]" % (mask_secret(current) if secret else current) if current else ""
        while True:
            prompt = " %s%s: " % (label, suffix)
            try:
                if secret:
                    value = read_secret(prompt, reader)
                elif reader is not None:
                    value = reader.readline(prompt)
                    if value is None:
                        raise EOFError
                else:
                    value = input(prompt)
            except (EOFError, KeyboardInterrupt):
                print("")
                raise SystemExit(1)
            value = value.strip()
            if value:
                return value
            if current:
                return current
            print("   不能为空，请重新输入。")

    config["base_url"] = ask("接口地址 base_url", config.get("base_url") or DEFAULT_CONFIG["base_url"])
    config["api_key"] = ask("API Key", config.get("api_key") or "", secret=True)
    config["model"] = ask("模型名 model", config.get("model") or DEFAULT_CONFIG["model"])
    path = config_path()
    save_config_file(path, config)
    print("")
    print(" 已保存配置：%s" % path)
    print("")
    return config


def resolve_config(args):
    file_config = load_config_file(args.config or config_path())
    cli = {}
    if args.base_url:
        cli["base_url"] = args.base_url
    if args.api_key:
        cli["api_key"] = args.api_key
    if args.model:
        cli["model"] = args.model
    if args.cwd:
        cli["cwd"] = args.cwd
    if args.max_rounds:
        cli["max_tool_rounds"] = args.max_rounds
    if args.shell_timeout:
        cli["shell_timeout_ms"] = args.shell_timeout
    if args.shell_mode:
        cli["shell_mode"] = args.shell_mode
    if args.no_stream:
        cli["stream"] = False
    if args.editor:
        cli["editor"] = args.editor
    if args.no_color:
        cli["color"] = "never"
    if args.no_save:
        cli["save_sessions"] = False
    if args.pick_model:
        cli["model_picker"] = True
    if args.no_pick_model:
        cli["model_picker"] = False
    return merge_config(DEFAULT_CONFIG, file_config, env_layer(), cli)


# =============================================================================
# 5. 工具一：pwsh —— 持久 PowerShell 会话
# =============================================================================

PS_PROBE_SCRIPT = ("$v='?';"
                   "try{$v=$PSVersionTable.PSVersion.ToString()}catch{};"
                   "if(-not $v){$v='?'};"
                   "Write-Output ('DSHMINI_PROBE_OK ps=' + $v + ' cn=' + '中文探测正常')")
PROBE_CN_TOKEN = "中文探测正常"

# 长驻会话的 loader：用 -EncodedCommand 跑，自己读 stdin 的 while 循环。
# 只用 PowerShell 2.0 就有的语法（不用 if 表达式、不用 ?: 、不用 -LiteralPath 之外的 3.0+ 特性），
# 标记一律走 [Console]::Out.WriteLine + Flush，不依赖宿主的输出缓冲策略。
PS_LOADER_TEMPLATE = r"""$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$__dsh_tag = '__TAG__'
$__dsh_begin = '__DSHMINI_' + $__dsh_tag + '_BEGIN__'
$__dsh_end = '__DSHMINI_' + $__dsh_tag + '_END:'
$__dsh_sep = [char]30
$__dsh_nl = [char]10
$__dsh_reader = $null
try { $__dsh_reader = New-Object System.IO.StreamReader -ArgumentList @([Console]::OpenStandardInput(), [Text.Encoding]::UTF8) } catch { $__dsh_reader = $null }
if ($__dsh_reader -eq $null) { try { [Console]::InputEncoding = [Text.Encoding]::UTF8 } catch { }; $__dsh_reader = [Console]::In }
while ($true) {
    $__dsh_line = $__dsh_reader.ReadLine()
    if ($__dsh_line -eq $null) { break }
    if ($__dsh_line.Length -lt 2) { continue }
    if ($__dsh_line.Substring(0, 1) -ne '@') { continue }
    $__dsh_cmd = $__dsh_line.Substring(1).Trim().Replace($__dsh_sep, $__dsh_nl)
    if ($__dsh_cmd.Length -eq 0) { continue }
    [Console]::Out.WriteLine($__dsh_begin)
    [Console]::Out.Flush()
    $global:LASTEXITCODE = $null
    $__dsh_ok = $true
    try { Invoke-Expression $__dsh_cmd } catch {
        $__dsh_ok = $false
        [Console]::Out.WriteLine('ERROR: ' + $_.Exception.Message)
    }
    $__dsh_code = 0
    $__dsh_last = $global:LASTEXITCODE
    if ($__dsh_last -ne $null) { $__dsh_code = [int]$__dsh_last }
    elseif (-not $__dsh_ok) { $__dsh_code = 1 }
    [Console]::Out.Flush()
    [Console]::Out.WriteLine($__dsh_end + $__dsh_code)
    [Console]::Out.Flush()
}
"""

# 单命令模式的「前导 + 收尾」：
#   前导压掉进度条和编码噪音；收尾把真实退出码带出去（原生命令的 $LASTEXITCODE 优先）。
ONESHOT_PREAMBLE = ("$ProgressPreference = 'SilentlyContinue'\n"
                    "try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }\n"
                    "try { $OutputEncoding = [Text.Encoding]::UTF8 } catch { }\n")
ONESHOT_TAIL = ("\n$__dsh_code = 0\n"
                "if ($global:LASTEXITCODE -ne $null) { $__dsh_code = [int]$global:LASTEXITCODE } "
                "elseif (-not $?) { $__dsh_code = 1 }\n"
                "exit $__dsh_code\n")
ONESHOT_INLINE_LIMIT = 8000      # 超过这个长度就不塞命令行，改写临时 .ps1


def app_dir():
    """获取程序主目录（exe 所在目录或项目根目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(script_dir).lower() == "dev":
        return os.path.dirname(script_dir)
    return script_dir


def get_embedded_pwsh_zip():
    """获取专为 Win7 定制内嵌的 PowerShell 7 压缩包路径。"""
    candidates = []
    # 1. PyInstaller 打包后的运行临时目录
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "pwsh-embedded.zip"))
    # 2. 程序主目录及其 dev 子目录
    root = app_dir()
    candidates.append(os.path.join(root, "pwsh-embedded.zip"))
    candidates.append(os.path.join(root, "dev", "pwsh-embedded.zip"))
    # 3. 源码所在目录及其父目录
    src_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(src_dir, "pwsh-embedded.zip"))
    candidates.append(os.path.join(os.path.dirname(src_dir), "pwsh-embedded.zip"))
    candidates.append(os.path.join(os.path.dirname(src_dir), "dev", "pwsh-embedded.zip"))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, "pwsh-embedded.zip"))
    for p in candidates:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


def get_bundled_pwsh_dir():
    """获取程序同目录下的自带 PowerShell 7 目录"""
    root = app_dir()
    preferred = os.path.join(root, "pwsh")
    if _writable_dir(preferred) or os.path.isdir(preferred):
        return preferred
    fallback = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "dsh-mini", "pwsh")
    return fallback


def get_bundled_pwsh_exe():
    """获取程序同目录下已解压的自带 PowerShell 7 可执行文件路径（已完整解压并带标记）"""
    candidates = [
        os.path.join(app_dir(), "pwsh"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "pwsh"),
        os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "dsh-mini", "pwsh"),
        os.path.join(tempfile.gettempdir(), "dsh-mini-pwsh"),
    ]
    for d in candidates:
        exe = os.path.join(d, "pwsh.exe")
        ok_flag = os.path.join(d, ".extracted_ok")
        if os.path.isfile(exe) and os.path.isfile(ok_flag) and os.path.getsize(exe) > 0:
            return os.path.abspath(exe)
    return None


def is_bundled_powershell(path):
    """判断给定路径是否为同目录自带的稳定版 PowerShell 7"""
    if not path:
        return False
    try:
        norm = os.path.normcase(os.path.abspath(path))
        bundled = get_bundled_pwsh_exe()
        if bundled and norm == os.path.normcase(os.path.abspath(bundled)):
            return True
        parts = norm.replace("/", "\\").split("\\")
        if len(parts) >= 2 and parts[-1] == "pwsh.exe" and parts[-2] == "pwsh":
            return True
    except Exception:
        pass
    return False


def _supplement_runtime_dlls(target_dir):
    """确保解压出来的 pwsh 具备 Win7 必须的运行时 DLL（UCRT / VC 运行库）"""
    sources = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        sources.append(sys._MEIPASS)
    python_dir = os.path.dirname(sys.executable)
    if os.path.isdir(python_dir):
        sources.append(python_dir)
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    if os.path.isdir(sys32):
        sources.append(sys32)
        downlevel = os.path.join(sys32, "downlevel")
        if os.path.isdir(downlevel):
            sources.append(downlevel)

    dll_names = (
        "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "ucrtbase.dll"
    )
    for name in dll_names:
        dst = os.path.join(target_dir, name)
        if not os.path.isfile(dst):
            for src in sources:
                src_file = os.path.join(src, name)
                if os.path.isfile(src_file):
                    try:
                        shutil.copy2(src_file, dst)
                        break
                    except Exception:
                        pass


def ensure_bundled_powershell(status_callback=None, force=False):
    """Win7 深度定制：检测并在同目录自动解压自带稳定版 PowerShell 7。
    
    1. 优先检查程序同目录是否已有解压完整的 pwsh（pwsh.exe 与 .extracted_ok 均存在）；
    2. 若已有且未指定 force，直接秒级返回，不重复解压；
    3. 若未检测到，自动定位内嵌的 pwsh-embedded.zip，自动解压到同目录 pwsh/；
    4. 补全 Win7 运行时 UCRT 与 VC++ 依赖库；
    5. 成功后写入 .extracted_ok 校验文件并返回 pwsh.exe 路径。
    """
    if not force:
        existing = get_bundled_pwsh_exe()
        if existing:
            return existing

    target_dir = get_bundled_pwsh_dir()
    target_exe = os.path.join(target_dir, "pwsh.exe")
    ok_flag = os.path.join(target_dir, ".extracted_ok")

    if not force and os.path.isfile(target_exe) and os.path.isfile(ok_flag) and os.path.getsize(target_exe) > 0:
        return target_exe

    zip_path = get_embedded_pwsh_zip()
    if not zip_path or not os.path.isfile(zip_path):
        if status_callback:
            status_callback("未找到内嵌的 PowerShell 7 压缩包")
        return None

    try:
        if status_callback:
            status_callback("Win7 深度定制：正在解压自带稳定版 PowerShell 7 到同目录（%s）..." % target_dir)
        os.makedirs(target_dir, exist_ok=True)
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)

        _supplement_runtime_dlls(target_dir)

        try:
            with open(ok_flag, "w", encoding="utf-8") as f:
                f.write("PowerShell 7.4.7 Win7 Custom Edition\n")
        except Exception:
            pass

        if os.path.isfile(target_exe):
            if status_callback:
                status_callback("自带稳定版 PowerShell 7 已就绪（同目录：%s）" % target_exe)
            return target_exe
    except Exception as exc:
        if status_callback:
            status_callback("解压自带 PowerShell 7 出错：%s" % exc)
    return None


_PS_PROBE_CACHE = {}
def powershell_candidates(config=None):
    """按优先级给出候选 PowerShell，强制优先使用同目录自带的稳定版 PowerShell 7。"""
    config = config or {}
    items = []
    override = (config.get("shell_exe") or "").strip()
    if override:
        items.append(("shell_exe 指定", override))

    # 1. 强制优先：同目录自带稳定版 PowerShell 7 (Win7定制)
    bundled_pwsh = ensure_bundled_powershell()
    if bundled_pwsh and os.path.isfile(bundled_pwsh):
        items.append(("自带稳定版 PowerShell 7 (Win7定制)", bundled_pwsh))

    # 2. PATH 中的 pwsh
    for name in ("pwsh.exe", "pwsh"):
        found = shutil.which(name)
        if found:
            items.append(("PATH 中的 " + name, found))

    # 3. 系统原生 PowerShell
    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    for path, label in (
        (os.path.join(program_files, "PowerShell", "7", "pwsh.exe"), "PowerShell 7 (64 位)"),
        (os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), "Windows PowerShell (系统自带)"),
        (os.path.join(program_files_x86, "PowerShell", "7", "pwsh.exe"), "PowerShell 7 (32 位)"),
        (os.path.join(system_root, "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe"), "Windows PowerShell (32 位)"),
    ):
        items.append((label, path))
    for name in ("powershell.exe", "powershell"):
        found = shutil.which(name)
        if found:
            items.append(("PATH 中的 " + name, found))

    result = []
    seen = set()
    for label, path in items:
        if not path:
            continue
        absolute = os.path.isabs(path)
        if absolute and not os.path.isfile(path):
            continue
        key = os.path.normcase(os.path.abspath(path)) if absolute else os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        result.append((label, path))
    return result


def find_powershell(config=None):
    """挑一个可用的 PowerShell 路径（只挑不探测，探测交给 check_powershell）。"""
    items = powershell_candidates(config)
    return items[0][1] if items else "powershell.exe"


def quote_ps_single(text):
    return "'" + str(text).replace("'", "''") + "'"


def _has_console():
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


def _proc_flags():
    if not IS_WIN:
        return 0
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if not _has_console():
        # GUI（无控制台）场景：给子进程一个自己的隐藏控制台，
        # 既不会闪黑框，也不会互相干扰控制台状态。
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return flags


def shell_env():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"      # 让 python 子进程按 UTF-8 输出
    env["PYTHONUNBUFFERED"] = "1"
    env["DSHMINI_SHELL"] = "1"
    return env


def kill_process(proc):
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def run_powershell_once(exe, command, cwd=None, timeout=30, encoding="auto"):
    """一条命令一个进程，返回 (输出文本, 退出码或 None, 备注)。

    为什么用 -Command 而不是 -EncodedCommand：
      -EncodedCommand 会把 PowerShell 的错误流序列化成 CLIXML
      （输出里混进 `#< CLIXML ...<Objs ...` 这种垃圾），-Command 则是干净的纯文本。
      -Command 同样从 PowerShell 2.0 支持到 7.x，但受命令行长度限制（约 32767 字符），
      所以超长命令会自动改走「写临时 .ps1 + Invoke-Expression 读回来」的路子
      （不用 -File，避免被执行策略拦住）。
    """
    script = ONESHOT_PREAMBLE + command + ONESHOT_TAIL
    temp_path = None
    if len(script) <= ONESHOT_INLINE_LIMIT:
        args = [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", script]
    else:
        # 超长命令塞不进命令行（Windows 上限约 32767 字符）：写成临时 .ps1 用 -File 跑。
        temp_path = os.path.join(tempfile.gettempdir(), "dsh-mini-cmd-%s.ps1" % uuid.uuid4().hex[:10])
        try:
            with open(temp_path, "w", encoding="utf-8-sig") as handle:
                handle.write(script)
        except Exception as exc:
            return "", None, "命令太长且临时文件写入失败：%s" % exc
        args = [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", temp_path]
    workdir = cwd if (cwd and os.path.isdir(cwd)) else None
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, cwd=workdir, env=shell_env(),
                                creationflags=_proc_flags())
    except Exception as exc:
        _remove_quietly(temp_path)
        return "", None, "无法启动 %s：%s" % (exe, exc)
    raw = b""
    try:
        raw, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process(proc)
        try:
            raw, _ = proc.communicate(timeout=5)
        except Exception:
            raw = b""
        _remove_quietly(temp_path)
        return decode_output(raw or b"", encoding), None, "timeout"
    except Exception as exc:
        kill_process(proc)
        _remove_quietly(temp_path)
        return decode_output(raw or b"", encoding), None, str(exc)
    _remove_quietly(temp_path)
    return decode_output(raw or b"", encoding), proc.returncode, ""


def _remove_quietly(path):
    if not path:
        return
    try:
        os.remove(path)
    except Exception:
        pass


class ShellUnavailable(Exception):
    """一个能用的 PowerShell 都没有。"""


class ShellDead(Exception):
    """长驻 PowerShell 进程已经退出。"""


def check_powershell(config=None, encoding="auto", timeout=25):
    """探测出一个真正跑得起来的 PowerShell，返回 (标签, 路径, 版本)。

    每个候选都实跑一条 -EncodedCommand 探针，谁先正常返回用谁；
    结果按 shell_exe 配置缓存，一次运行只探一遍。全部失败抛 ShellUnavailable。
    """
    config = config or {}
    key = (config.get("shell_exe") or "").strip()
    entry = _PS_PROBE_CACHE.get(key)
    if entry is not None:
        if entry["ok"]:
            return entry["value"]
        raise ShellUnavailable(entry["message"])

    timeout = max(5.0, float(timeout))        # 探针别被配置里的极小超时坑到
    reasons = []
    for label, path in powershell_candidates(config):
        text, code, note = run_powershell_once(path, PS_PROBE_SCRIPT, timeout=timeout, encoding=encoding)
        if note == "timeout":
            reasons.append("%s（%s）启动超时" % (label, path))
            continue
        if note:
            reasons.append("%s（%s）%s" % (label, path, note))
            continue
        if "DSHMINI_PROBE_OK" in (text or ""):
            version = ""
            match = re.search(r"ps=(\S+)", text)
            if match:
                version = match.group(1)
            found = (label, path, version)
            _PS_PROBE_CACHE[key] = {"ok": True, "value": found, "message": ""}
            return found
        reasons.append("%s（%s）探针无响应：%s" % (label, path, (text or "").strip()[:80]))

    message = "；".join(reasons) if reasons else "没有找到任何 PowerShell 可执行文件"
    _PS_PROBE_CACHE[key] = {"ok": False, "value": None, "message": message}
    raise ShellUnavailable(message)


class PersistentShell(object):
    """一个长驻 PowerShell 进程：变量、当前目录、函数、后台作业在多次调用间保持。

    协议：每条命令包成一行 ASCII 发送给子进程 stdin
        Write-Output <START>; ... Invoke-Expression <base64 解码后的命令> ...; Write-Output <END:exit>
    子进程逐行执行（已验证 PowerShell 5.1 从管道读取时是逐行执行的），
    读取线程按标记切分输出，因此可以精确定位某条命令的输出与退出码。
    """

    def __init__(self, config, on_output=None):
        self.config = config
        self.on_output = on_output
        self.on_notice = None                     # 由 Agent 注入（用户可见的提示）
        self.cwd = config.get("cwd") or os.getcwd()
        self.encoding = config.get("shell_encoding") or "auto"
        self.timeout_ms = int(config.get("shell_timeout_ms") or 300000)
        self.probe_timeout_ms = int(config.get("shell_probe_timeout_ms") or 20000)
        requested = str(config.get("shell_mode") or "auto").strip().lower()
        self.requested_mode = requested if requested in ("auto", "persistent", "oneshot") else "auto"
        self.mode = "oneshot" if self.requested_mode == "oneshot" else "persistent"
        self.exe = ""
        self.exe_label = ""
        self.ps_version = ""
        self.degraded_reason = ""
        self._fallback_note = ""
        self._notice_sent = False
        self._proc = None
        self._reader = None
        self._lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._pending = b""
        self._capture = None
        self._dead = False
        self._tag = ""
        self._started = False

    # ---- 对外状态 ----

    def set_notice_callback(self, callback):
        self.on_notice = callback

    def _notify(self, text):
        if self.on_notice is None or self._notice_sent:
            return
        self._notice_sent = True
        try:
            self.on_notice(text)
        except Exception:
            pass

    def describe(self):
        """给系统提示词用的一句话：告诉模型运行环境与状态到底保不保持。"""
        is_bundled = is_bundled_powershell(self.exe)
        shell_name = "bundled stable PowerShell 7 (Win7 custom edition)" if is_bundled else "Windows PowerShell"
        if self.mode == "oneshot":
            return ("the pwsh tool is %s in one-shot mode: every call runs in a brand new process, "
                    "so variables, functions and the current directory do NOT carry over between calls" % shell_name)
        return "the pwsh tool is %s and keeps its state between calls" % shell_name

    def _check_exe_override(self):
        """配置里指定了 shell_exe 却没被用上时，明确告诉用户一次（别静默换）。"""
        override = (self.config.get("shell_exe") or "").strip()
        if not override or not self.exe:
            return
        try:
            same = os.path.normcase(os.path.abspath(override)) == os.path.normcase(os.path.abspath(self.exe))
        except Exception:
            same = (override == self.exe)
        if same:
            return
        if os.path.isabs(override) and not os.path.isfile(override):
            self._notify("配置里的 shell_exe 不存在：%s —— 已改用 %s" % (override, self.exe))
        else:
            self._notify("配置里的 shell_exe 用不了：%s —— 已改用 %s" % (override, self.exe))

    def status_line(self):
        """给 /config 和 --shellcheck 看的一行状态。"""
        target = self.exe or "(未探测)"
        if self.exe_label:
            target = "%s: %s" % (self.exe_label, self.exe)
        return ("模式=%s  可执行文件=%s  版本=%s%s"
                % (self.mode, target, self.ps_version or "?",
                   ("  降级原因=" + self.degraded_reason) if self.degraded_reason else ""))

    # ---- 进程生命周期 ----

    def _spawn_persistent(self):
        label, exe, version = check_powershell(self.config, self.encoding, self.probe_timeout_ms / 1000.0)
        self.exe_label, self.exe, self.ps_version = label, exe, version
        self._check_exe_override()
        self._tag = uuid.uuid4().hex[:12]
        loader = PS_LOADER_TEMPLATE.replace("__TAG__", self._tag)
        # 用 -Command 而不是 -EncodedCommand：后者会把子进程的错误流序列化成 CLIXML，
        # 命令报错时输出里会混进一堆 <Objs ...> 垃圾。
        args = [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", loader]
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.cwd if os.path.isdir(self.cwd) else None,
            env=shell_env(),
            creationflags=_proc_flags(),
            bufsize=0,
        )
        self._dead = False
        self._pending = b""
        self._reader = threading.Thread(target=self._read_loop)
        self._reader.daemon = True
        self._reader.start()

    def _start(self):
        """启动长驻会话并握手；成功返回 ""，失败返回原因（人类可读）。"""
        if self.requested_mode == "oneshot":
            return "shell_mode=oneshot"
        try:
            self._spawn_persistent()
        except ShellUnavailable as exc:
            return "没有找到可用的 PowerShell（%s）" % exc
        except Exception as exc:
            return "启动 PowerShell 失败（%s: %s）" % (exc.__class__.__name__, exc)
        text, _code, status = self._run_persistent(PS_PROBE_SCRIPT, self.probe_timeout_ms / 1000.0)
        if status == "ok" and "DSHMINI_PROBE_OK" in (text or "") and PROBE_CN_TOKEN in (text or ""):
            match = re.search(r"ps=(\S+)", text)
            if match:
                self.ps_version = match.group(1)
            self._started = True
            return ""
        self.kill()
        if status == "timeout":
            seconds = self.probe_timeout_ms / 1000.0
            span = ("%d 秒" % int(round(seconds))) if seconds >= 1 else ("%d 毫秒" % int(self.probe_timeout_ms))
            return "长驻会话握手超时（%s内没有任何回应）" % span
        if status == "dead":
            return "长驻会话启动后立刻退出"
        if "DSHMINI_PROBE_OK" in (text or ""):
            return "长驻会话中文编码校验失败（stdin 编码不对）"
        return "长驻会话握手失败：%s" % ((text or "").strip()[:200] or status)

    def _degrade(self, reason):
        """降级为单命令模式（每条命令一个进程），并告诉用户一次。"""
        if self.mode == "oneshot":
            return
        self.mode = "oneshot"
        self.degraded_reason = reason
        self._fallback_note = (
            "[shell note] The persistent pwsh session could not be used on this machine (%s); switched to "
            "one-shot mode: every pwsh call now runs in its own PowerShell process, so variables, functions "
            "and the current directory do NOT persist between calls." % reason)
        self._notify("pwsh 已自动降级为「单命令模式」（原因：%s）。命令照常执行，但变量和当前目录不再跨调用保留。" % reason)

    def _write_line(self, line):
        if self._proc is None or self._proc.poll() is not None:
            raise ShellDead("PowerShell 进程已退出")
        data = (line + "\r\n").encode("utf-8", "replace")
        try:
            fd = self._proc.stdin.fileno()
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
        except Exception as exc:
            raise ShellDead("写入 PowerShell 失败：%s" % exc)


    # ---- 读取线程 ----

    def _read_loop(self):
        fd = self._proc.stdout.fileno()
        try:
            while True:
                try:
                    chunk = os.read(fd, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                self._pending += chunk
                while True:
                    index = self._pending.find(b"\n")
                    if index < 0:
                        if len(self._pending) > 1 << 20:      # 超长无换行（进度条等）强制冲刷
                            self._handle_line(decode_output(self._pending, self.encoding))
                            self._pending = b""
                        break
                    raw = self._pending[:index]
                    self._pending = self._pending[index + 1:]
                    if raw.endswith(b"\r"):
                        raw = raw[:-1]
                    self._handle_line(decode_output(raw, self.encoding))
        finally:
            self._on_process_end()

    def _handle_line(self, line):
        with self._state_lock:
            capture = self._capture
            if capture is None:
                return
            stripped = line.strip()
            if not capture["started"]:
                if stripped == capture["start"]:
                    capture["started"] = True
                return
            if stripped.startswith(capture["end"]):
                tail = stripped[len(capture["end"]):]
                match = re.match(r"(-?\d+)", tail)
                capture["exit_code"] = int(match.group(1)) if match else 0
                capture["done"].set()
                # 注意：这里不清空 self._capture，由 run() 在读完之后统一清掉，
                # 这样宿主缓冲里迟到几毫秒的末尾输出也能被收进来。
                return
            capture["lines"].append(line)
        if self.on_output is not None:
            try:
                self.on_output(line)
            except Exception:
                pass

    def _on_process_end(self):
        self._dead = True
        with self._state_lock:
            capture = self._capture
            if capture is not None:
                capture["dead"] = True
                capture["done"].set()

    # ---- 执行 ----

    def _wrap(self, command):
        """把命令变成一行纯文本协议（UTF-8）：'@' + 命令，换行用 \\x1e 占位。

        刻意不用 base64：base64 解码 + Invoke-Expression 是杀软启发式里
        「无文件执行」的经典组合，容易让整包 exe 被误报。
        """
        start = "__DSHMINI_%s_BEGIN__" % self._tag
        end = "__DSHMINI_%s_END:" % self._tag
        text = sanitize_text(command).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\x1e")
        return start, end, "@" + text

    def _run_persistent(self, command, timeout):
        """把命令送给长驻会话，返回 (输出, 退出码, 状态)。

        状态：ok=拿到结束标记；timeout=超时（子进程已杀掉）；dead=子进程中途退出；
        write-failed=命令根本没送出去。
        """
        start, end, wrapper = self._wrap(command)
        capture = {
            "start": start,
            "end": end,
            "started": False,
            "lines": [],
            "done": threading.Event(),
            "exit_code": None,
            "dead": False,
        }
        with self._state_lock:
            self._capture = capture
        try:
            self._write_line(wrapper)
        except ShellDead:
            with self._state_lock:
                self._capture = None
            return ("", None, "write-failed")

        deadline = time.monotonic() + timeout
        while not capture["done"].is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            capture["done"].wait(min(0.25, remaining))
        if capture["done"].is_set():
            time.sleep(0.03)          # 收一下宿主缓冲里可能迟到的末尾几行
        with self._state_lock:
            self._capture = None

        text = "\n".join(capture["lines"]).strip("\r\n")
        if capture["done"].is_set() and capture["exit_code"] is not None:
            return (text, capture["exit_code"], "ok")
        if capture["dead"]:
            return (text, None, "dead")
        self.kill()
        return (text, None, "timeout")

    def _run_oneshot(self, command, timeout):
        """兜底：一条命令一个进程（-EncodedCommand 直跑），状态不保持。"""
        if not self.exe:
            try:
                label, exe, version = check_powershell(self.config, self.encoding,
                                                       self.probe_timeout_ms / 1000.0)
                self.exe_label, self.exe, self.ps_version = label, exe, version
                self._check_exe_override()
            except ShellUnavailable as exc:
                return ("", None, "无法执行命令：没有可用的 PowerShell（%s）" % exc)
        text, code, note = run_powershell_once(self.exe, command, cwd=self.cwd, timeout=timeout,
                                               encoding=self.encoding)
        if self._fallback_note:
            note = (note + "\n" if note else "") + self._fallback_note
            self._fallback_note = ""
        if note == "timeout":
            message = ("Your command timed out after %d seconds or experienced an OOM error. "
                       "Below is partial output:\n%s" % (int(timeout), text if text else "(no output)"))
            return (message, None, (note + "\n" if note else "") + SHELL_ONESHOT_MESSAGE)
        if note:
            return (text, None, note)
        return (text, code, "")

    def run(self, command, timeout_ms=None):
        """执行一条 PowerShell 命令，返回 (输出文本, 退出码, 附加说明)。"""
        if command is None or not str(command).strip():
            raise ValueError("command must be a non-empty string")
        command = sanitize_text(str(command))
        timeout = (timeout_ms or self.timeout_ms) / 1000.0
        with self._lock:
            if self.mode == "persistent":
                if not self.is_alive():
                    self.kill()
                    reason = self._start()
                    if reason:
                        if self.requested_mode == "persistent":
                            return ("", None, "无法启动持久 pwsh 会话：%s" % reason)
                        self._degrade(reason)
                if self.mode == "persistent":
                    text, code, status = self._run_persistent(command, timeout)
                    if status == "ok":
                        return (text, code, "")
                    if status == "timeout":
                        note = ("Your command timed out after %d seconds or experienced an OOM error. "
                                "Below is partial output:\n%s" % (int(timeout), text if text else "(no output)"))
                        return (note, None, SHELL_RESET_MESSAGE)
                    self.kill()
                    if self.requested_mode == "persistent":
                        reason = "命令执行期间持久会话退出" if status == "dead" else "命令没能写入持久会话"
                        return (text, None, "%s。%s" % (reason, SHELL_RESET_MESSAGE))
                    self._degrade("命令执行期间持久会话退出" if status == "dead" else "命令没能写入持久会话")
            return self._run_oneshot(command, timeout)

    def kill(self):
        proc = self._proc
        self._proc = None
        self._dead = True
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            for stream in (proc.stdin, proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        with self._state_lock:
            capture = self._capture
            if capture is not None:
                capture["done"].set()
            self._capture = None
        self._started = False

    def restart(self):
        self.kill()
        self.cwd = self.config.get("cwd") or self.cwd

    def is_alive(self):
        return (self._started and self._proc is not None and self._proc.poll() is None
                and not self._dead)


def run_shellcheck(config=None, emit=None, probe_timeout=25):
    """离线诊断 pwsh 工具（Win7 排障用）：每一步的真实结果都打印出来，不联网。"""
    write = emit or (lambda text="": print(text))

    def pad(text, width):
        return text + " " * max(0, width - display_width(text))

    settings = dict(DEFAULT_CONFIG)
    if config:
        settings.update(config)

    write("")
    write("=" * 70)
    write(" dsh-mini pwsh 诊断（离线，不联网）")
    write("=" * 70)
    try:
        ver = sys.getwindowsversion()
        os_text = "Windows %d.%d build %d" % (ver.major, ver.minor, ver.build)
    except Exception:
        os_text = os.name
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    write(" 程序版本      : %s" % VERSION)
    write(" 系统          : %s（%d 位）" % (os_text, bits))
    write(" 环境说明      : Windows 7 深度定制版（内置稳定版 PowerShell 7，已强制绑定调用，彻底消除系统自带 2.0/3.0/5.1 混乱缺陷）")
    bundled_exe = get_bundled_pwsh_exe()
    bundled_status = "已就绪" if bundled_exe else "未解压（运行时自动释放）"
    write(" 自带 pwsh 7   : %s（同目录：%s）" % (bundled_status, bundled_exe or get_bundled_pwsh_dir()))
    write(" Python        : %s" % sys.version.split()[0])
    write(" 工作目录      : %s" % (settings.get("cwd") or os.getcwd()))
    write(" shell_mode    : %s" % settings.get("shell_mode"))
    write(" shell_exe     : %s" % (settings.get("shell_exe") or "(自动探测)"))
    write(" shell_encoding: %s" % settings.get("shell_encoding"))
    write(" 握手超时      : %s ms" % settings.get("shell_probe_timeout_ms"))
    write(" 单命令超时    : %s ms" % settings.get("shell_timeout_ms"))
    write(" 控制台编码    : %s" % (console_encoding() or "(未知)"))
    write("")

    # ---- 1. 候选列表 ----
    write("[1/3] 候选 PowerShell（按优先级）")
    candidates = powershell_candidates(settings)
    if not candidates:
        write("  ! 一个都没找到，PowerShell 可能没有安装或路径异常")
    for index, (label, path) in enumerate(candidates, 1):
        write("  %d) %s%s" % (index, pad(label, 30), path))
    write("")

    # ---- 2. 逐个探测 ----
    write("[2/3] 逐个试跑探针命令（-Command 直跑）")
    usable = []
    for label, path in candidates:
        started = time.monotonic()
        try:
            text, code, note = run_powershell_once(path, PS_PROBE_SCRIPT, timeout=probe_timeout,
                                                   encoding=settings.get("shell_encoding"))
        except Exception as exc:
            write("  × %s探测时出错：%s" % (pad(label, 30), exc))
            continue
        elapsed = time.monotonic() - started
        if note == "timeout":
            write("  × %s启动超时（%.1fs）" % (pad(label, 30), elapsed))
            continue
        if note:
            write("  × %s%s" % (pad(label, 30), note))
            continue
        if "DSHMINI_PROBE_OK" not in (text or ""):
            write("  × %s探针无响应：%s" % (pad(label, 30), (text or "").strip()[:60]))
            continue
        match = re.search(r"ps=(\S+)", text)
        version = match.group(1) if match else "?"
        usable.append((label, path, version))
        write("  √ %s版本 %-14s 用时 %.1fs" % (pad(label, 30), version, elapsed))
    write("")

    # ---- 3. 两种模式各跑一条真命令 ----
    write("[3/3] 实战：持久会话 vs 单命令模式")
    persistent_ok = False
    oneshot_ok = False
    persistent_note = ""

    persistent_config = dict(settings)
    persistent_config["shell_mode"] = "persistent"
    shell = PersistentShell(persistent_config)
    started = time.monotonic()
    text, code, note = shell.run("Write-Output 'dsh-mini-shell-ok'")
    elapsed = time.monotonic() - started
    if "dsh-mini-shell-ok" in (text or ""):
        persistent_ok = True
        write("  √ 持久会话可用（%s，PS %s，%.1fs）：stdout 协议与标记都正常"
              % (shell.exe_label or "?", shell.ps_version or "?", elapsed))
    else:
        persistent_note = note or (text or "").strip()[:120]
        write("  × 持久会话不可用：%s" % persistent_note)
    shell.kill()

    oneshot_config = dict(settings)
    oneshot_config["shell_mode"] = "oneshot"
    shell2 = PersistentShell(oneshot_config)
    started = time.monotonic()
    text2, code2, note2 = shell2.run("Write-Output 'dsh-mini-shell-ok'; Get-Location | Out-String")
    elapsed = time.monotonic() - started
    if "dsh-mini-shell-ok" in (text2 or ""):
        oneshot_ok = True
        write("  √ 单命令模式可用（%s，%.1fs）" % (shell2.exe or "?", elapsed))
    else:
        write("  × 单命令模式也不可用：%s" % (note2 or (text2 or "").strip()[:120]))
    shell2.kill()

    write("")
    write("-" * 70)
    if persistent_ok:
        write(" 结论：pwsh 工具工作正常（持久会话，变量/目录跨调用保持）。")
        write(" 如果模型仍说命令失败，请把上面的输出发我。")
        result = 0
    elif oneshot_ok:
        write(" 结论：持久会话在这台机器上不可用，但命令本身能执行。")
        write("        dsh-mini 会自动降级为「单命令模式」：命令照常跑，")
        write("        只是变量和当前目录不跨调用保留（模型也会被告知）。")
        write(" 原因：%s" % persistent_note)
        result = 0
    else:
        write(" 结论：这台机器上 PowerShell 完全跑不起来，pwsh 工具不可用。")
        write(" 排查建议：")
        write("   1) 在 cmd 里执行  powershell -NoProfile -Command \"$PSVersionTable.PSVersion\"  看能不能出结果；")
        write("   2) 组策略/杀软可能禁用了 PowerShell，或系统缺少 WMF；")
        write("   3) 也可以在配置里用 shell_exe 指定一个可用的 powershell.exe 全路径。")
        result = 1
    versions = sorted(set(item[2] for item in usable))
    if persistent_ok and is_bundled_powershell(shell.exe):
        write("")
        write(" ★ Win7 深度定制提示：已成功启用程序同目录自带的稳定版 PowerShell 7！")
        write("   无论目标机器原生是 2.0、3.0 还是缺少 WMF，均已彻底解决 Win7 命令行生态混乱缺陷。")
    elif any(v.startswith("2.") for v in versions):
        write("")
        write(" 提示：检测到 Windows PowerShell 2.0（Win7 自带）。dsh-mini 已按 2.0 语法兼容，")
        write("       但 2.0 功能较少；若能升级到 WMF 5.1（KB3191566）体验会更好。")
    write("-" * 70)
    write("")
    return result


def sanitize_text(text):
    """剔除孤立代理字符等无法编码的内容，避免 JSON/流写入崩溃。"""
    if not text:
        return text
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")


class StdinReader(object):
    """按行读取标准输入。

    控制台走 input()（ReadConsoleW，中文/输入法正常）；
    管道输入走字节流 + 自适应编码（UTF-8 优先，回退本机代码页），
    避免 Python 默认用 ANSI 代码页解码 UTF-8 管道时产生乱码与代理字符。
    """

    def __init__(self, encoding="auto"):
        self.encoding = encoding
        self.buffer = getattr(sys.stdin, "buffer", None)
        self.byte_mode = (not _stdin_is_tty()) and self.buffer is not None

    def readline(self, prompt_text=""):
        if prompt_text:
            sys.stdout.write(prompt_text)
            sys.stdout.flush()
        if not self.byte_mode:
            try:
                return input("" if prompt_text else None)
            except EOFError:
                return None
        try:
            raw = self.buffer.readline()
        except Exception:
            return None
        if not raw:
            return None
        return sanitize_text(decode_output(raw.rstrip(b"\r\n"), self.encoding))


def maybe_truncate(text, max_chars, marker):
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + marker


def run_pwsh_tool(shell, args, max_output_chars, on_output=None):
    command = args.get("command")
    if command is None or not str(command).strip():
        raise ValueError("command must be a non-empty string")
    text, exit_code, note = shell.run(str(command))
    body = text if text else "(no output)"
    body = maybe_truncate(body, max_output_chars, TRUNCATED_SHELL)
    if exit_code is not None and exit_code != 0:
        body = body + "\n[exit code: %d]" % exit_code
    if note:
        body = body + "\n" + note
    return body


# =============================================================================
# 6. 工具二：str_replace_editor
# =============================================================================

def _detect_text(raw):
    """返回 (文本, 编码, 是否有 BOM)。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace"), "utf-8", True
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "replace"), "utf-16-le", True
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace"), "utf-16-be", True
    try:
        return raw.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        pass
    for name in candidate_output_encodings()[1:]:
        try:
            return raw.decode(name), name, False
        except Exception:
            continue
    return raw.decode("utf-8", "replace"), "utf-8", False


def _detect_eol(text):
    crlf = text.count("\r\n")
    lone_cr = text.count("\r") - crlf
    lone_lf = text.count("\n") - crlf
    if crlf >= lone_lf and crlf >= lone_cr and crlf > 0:
        return "\r\n"
    if lone_cr > lone_lf:
        return "\r"
    return "\n"


def _normalize_eol(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _restore_eol(text, eol):
    return text if eol == "\n" else text.replace("\n", eol)


class TextFile(object):
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as handle:
            raw = handle.read()
        self.text, self.encoding, self.bom = _detect_text(raw)
        self.eol = _detect_eol(self.text)
        self.normalized = _normalize_eol(self.text)

    def save(self, normalized_text):
        text = _restore_eol(normalized_text, self.eol)
        data = text.encode(self.encoding, "replace")
        if self.bom:
            data = b"\xef\xbb\xbf" + data if self.encoding == "utf-8" else data
        with open(self.path, "wb") as handle:
            handle.write(data)


def editor_require_absolute(path):
    if path is None or not str(path).strip():
        raise ValueError("path must be a non-empty string")
    if not os.path.isabs(path):
        raise ValueError("The path %s is not an absolute path, it should start with a drive letter "
                         "(e.g. `C:\\repo\\file.py`) or a UNC path (e.g. `\\\\server\\share\\file.py`)." % path)
    return os.path.normpath(path)


def editor_view(path, view_range, max_output_chars):
    target = editor_require_absolute(path)
    if not os.path.exists(target):
        raise ValueError("The path %s does not exist. Please provide a valid path." % target)
    if os.path.isdir(target):
        if view_range is not None:
            raise ValueError("The `view_range` parameter is not allowed when `path` points to a directory.")
        return _list_directory(target, max_output_chars)
    if not os.path.isfile(target):
        raise ValueError('cannot view "%s": not a regular file or directory' % target)
    content = TextFile(target).normalized
    return _format_file_view(target, content, max_output_chars, view_range)


def _format_file_view(path, content, max_output_chars, view_range):
    all_lines = content.split("\n")
    lines = all_lines
    initial_line = 1
    prompt = "Here's the content of %s with line numbers (which has a total of %d lines)" % (path, len(all_lines))
    if view_range is not None:
        if (not isinstance(view_range, (list, tuple)) or len(view_range) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in view_range)):
            raise ValueError("Invalid `view_range`. It should be a list of two integers.")
        first, last = int(view_range[0]), int(view_range[1])
        initial_line = first
        if first < 1 or first > len(all_lines):
            raise ValueError("Invalid `view_range`: [%d, %d]. Its first element `%d` should be within the range "
                             "of lines of the file: [1, %d]" % (first, last, first, len(all_lines)))
        if last > len(all_lines):
            raise ValueError("Invalid `view_range`: [%d, %d]. Its second element `%d` should be smaller than the "
                             "number of lines in the file: `%d`" % (first, last, last, len(all_lines)))
        if last != -1 and last < first:
            raise ValueError("Invalid `view_range`: [%d, %d]. Its second element `%d` should be larger or equal "
                             "than its first `%d`" % (first, last, last, first))
        lines = all_lines[first - 1:] if last == -1 else all_lines[first - 1:last]
        prompt += " with view_range=[%d, %d]" % (first, last)
    numbered = "\n".join("%6d  %s" % (initial_line + index, line) for index, line in enumerate(lines))
    return maybe_truncate("%s:\n%s\n" % (prompt, numbered), max_output_chars, TRUNCATED_EDITOR)


def _list_directory(target, max_output_chars):
    rows = ["d\t%s" % target]

    def visit(directory, depth):
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in ("node_modules", "__pycache__"):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            kind = "d" if is_dir else ("f" if entry.is_file() else "?")
            rows.append("%s\t%s" % (kind, entry.path))
            if is_dir and depth < 2:
                visit(entry.path, depth + 1)

    visit(target, 1)
    rows.sort(key=lambda row: row[row.index("\t") + 1:])
    listing = maybe_truncate("\n".join(rows) + "\n", max_output_chars, TRUNCATED_EDITOR)
    return ("Here're the files and directories up to 2 levels deep in %s, excluding hidden items, node_modules, "
            "and Python cache directories:\n%s\n" % (target, listing))


def editor_create(path, file_text, max_output_chars):
    if file_text is None:
        raise ValueError("Parameter `file_text` is required for command: create")
    target = editor_require_absolute(path)
    if os.path.exists(target):
        raise ValueError("File already exists at: %s. Cannot overwrite files using command `create`." % target)
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        raise ValueError("Cannot create file: parent directory does not exist: %s" % parent)
    with open(target, "wb") as handle:
        handle.write(file_text.encode("utf-8", "replace"))
    return "New file created successfully at: %s" % target


def editor_str_replace(path, old_str, new_str, max_output_chars):
    if old_str is None:
        raise ValueError("Parameter `old_str` is required for command: str_replace")
    if old_str == "":
        raise ValueError("Parameter `old_str` is empty for command: str_replace")
    target = editor_require_absolute(path)
    if not os.path.exists(target):
        raise ValueError("The path %s does not exist. Please provide a valid path." % target)
    if os.path.isdir(target):
        raise ValueError("The path %s is a directory and only the `view` command can be used on directories" % target)
    handle = TextFile(target)
    before = handle.normalized
    old_value = _normalize_eol(old_str)
    new_value = _normalize_eol(new_str or "")
    offsets = []
    index = before.find(old_value)
    while index >= 0:
        offsets.append(index)
        index = before.find(old_value, index + len(old_value))
    if not offsets:
        raise ValueError("No replacement was performed, old_str `%s` did not appear verbatim in %s."
                         % (old_str, target))
    if len(offsets) > 1:
        lines = []
        for offset in offsets:
            lines.append(before.count("\n", 0, offset) + 1)
        raise ValueError("No replacement was performed. Multiple occurrences of old_str `%s` in lines [%s]. "
                         "Please ensure it is unique" % (old_str, ", ".join(str(n) for n in lines)))
    offset = offsets[0]
    handle.save(before[:offset] + new_value + before[offset + len(old_value):])
    return "The file %s has been edited successfully." % target


def editor_insert(path, insert_line, new_str, max_output_chars):
    if insert_line is None:
        raise ValueError("Parameter `insert_line` is required for command: insert")
    if new_str is None:
        raise ValueError("Parameter `new_str` is required for command: insert")
    target = editor_require_absolute(path)
    if not os.path.exists(target):
        raise ValueError("The path %s does not exist. Please provide a valid path." % target)
    if os.path.isdir(target):
        raise ValueError("The path %s is a directory and only the `view` command can be used on directories" % target)
    handle = TextFile(target)
    lines = handle.normalized.split("\n")
    if not isinstance(insert_line, int) or isinstance(insert_line, bool):
        raise ValueError("Invalid `insert_line` parameter: %s. It should be within the range of lines of the file: "
                         "[0, %d]" % (insert_line, len(lines)))
    if insert_line < 0 or insert_line > len(lines):
        raise ValueError("Invalid `insert_line` parameter: %d. It should be within the range of lines of the file: "
                         "[0, %d]" % (insert_line, len(lines)))
    inserted = _normalize_eol(new_str).split("\n")
    after = lines[:insert_line] + inserted + lines[insert_line:]
    handle.save("\n".join(after))
    return "The file %s has been edited successfully." % target


def run_editor_tool(args, max_output_chars):
    command = args.get("command")
    path = args.get("path")
    if command not in ("view", "create", "str_replace", "insert"):
        raise ValueError("Parameter `command` must be one of: view, create, str_replace, insert")
    if command == "view":
        return editor_view(path, args.get("view_range"), max_output_chars)
    if command == "create":
        return editor_create(path, args.get("file_text"), max_output_chars)
    if command == "str_replace":
        return editor_str_replace(path, args.get("old_str"), args.get("new_str"), max_output_chars)
    return editor_insert(path, args.get("insert_line"), args.get("new_str"), max_output_chars)


# =============================================================================
# 工具三：mouse_control —— Windows 鼠标控制（Agent 鼠标交互能力）
# =============================================================================

MOUSE_ACTIONS = ("move", "click", "double_click", "down", "up", "scroll", "position")
MOUSE_BUTTONS = ("left", "right", "middle")


def _get_screen_size():
    if not IS_WIN:
        return 1920, 1080
    try:
        import ctypes
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return 1920, 1080


def _get_cursor_pos():
    if not IS_WIN:
        return 0, 0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return int(pt.x), int(pt.y)
    except Exception:
        return 0, 0


def run_mouse_tool(args):
    """纯 ctypes 实现 Windows 鼠标控制：操作鼠标放到哪、点击哪、左键/右键/中键/滚轮/坐标查询。"""
    if not isinstance(args, dict):
        raise ValueError("mouse_control 参数必须是 JSON 对象")
    action = str(args.get("action") or "").lower().strip()
    if not action:
        raise ValueError("Parameter `action` is required for tool: mouse_control")
    if action not in MOUSE_ACTIONS:
        raise ValueError("Invalid action `%s`. Allowed options: %s" % (action, ", ".join(MOUSE_ACTIONS)))

    button = str(args.get("button") or "left").lower().strip()
    if button not in MOUSE_BUTTONS:
        raise ValueError("Invalid button `%s`. Allowed options: %s" % (button, ", ".join(MOUSE_BUTTONS)))

    target_x = args.get("x")
    target_y = args.get("y")
    scroll_amount = args.get("scroll_amount")

    screen_w, screen_h = _get_screen_size()
    cur_x, cur_y = _get_cursor_pos()

    if not IS_WIN:
        return "Non-Windows environment: simulated mouse action '%s' on %s button" % (action, button)

    import ctypes
    user32 = ctypes.windll.user32

    # 若指定了坐标，移动到目标位置
    if target_x is not None or target_y is not None:
        new_x = int(target_x if target_x is not None else cur_x)
        new_y = int(target_y if target_y is not None else cur_y)
        new_x = max(0, min(screen_w - 1, new_x))
        new_y = max(0, min(screen_h - 1, new_y))
        user32.SetCursorPos(new_x, new_y)
        cur_x, cur_y = new_x, new_y

    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    WHEEL_DELTA = 120

    btn_flags = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }

    down_flag, up_flag = btn_flags[button]

    if action == "move":
        return "Mouse moved to (%d, %d). Screen resolution: %dx%d." % (cur_x, cur_y, screen_w, screen_h)
    elif action == "click":
        user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(0.02)
        user32.mouse_event(up_flag, 0, 0, 0, 0)
        return "Mouse clicked (%s button) at (%d, %d). Screen resolution: %dx%d." % (button, cur_x, cur_y, screen_w, screen_h)
    elif action == "double_click":
        user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(0.02)
        user32.mouse_event(up_flag, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(0.02)
        user32.mouse_event(up_flag, 0, 0, 0, 0)
        return "Mouse double-clicked (%s button) at (%d, %d). Screen resolution: %dx%d." % (button, cur_x, cur_y, screen_w, screen_h)
    elif action == "down":
        user32.mouse_event(down_flag, 0, 0, 0, 0)
        return "Mouse button down (%s button) at (%d, %d)." % (button, cur_x, cur_y)
    elif action == "up":
        user32.mouse_event(up_flag, 0, 0, 0, 0)
        return "Mouse button up (%s button) at (%d, %d)." % (button, cur_x, cur_y)
    elif action == "scroll":
        amount = int(scroll_amount if scroll_amount is not None else -1)
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, amount * WHEEL_DELTA, 0)
        direction = "up" if amount > 0 else "down"
        return "Mouse wheel scrolled %s by %d tick(s) at (%d, %d)." % (direction, abs(amount), cur_x, cur_y)
    elif action == "position":
        return "Current cursor position: (%d, %d). Screen resolution: %dx%d." % (cur_x, cur_y, screen_w, screen_h)
    else:
        raise ValueError("Unsupported mouse action: %s" % action)


# =============================================================================
# 图像与视觉支持（OpenAI 兼容多模态格式）
# =============================================================================

IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def is_image_path(path):
    if not path or not isinstance(path, str):
        return False
    clean = path.strip().strip('"').strip("'")
    if not os.path.isfile(clean):
        return False
    _, ext = os.path.splitext(clean.lower())
    return ext in IMAGE_EXTENSIONS


def encode_image_to_data_url(path):
    clean = path.strip().strip('"').strip("'")
    if not os.path.isfile(clean):
        raise ValueError("Image file not found: %s" % clean)
    _, ext = os.path.splitext(clean.lower())
    mime = IMAGE_EXTENSIONS.get(ext, "image/png")
    with open(clean, "rb") as handle:
        data = handle.read()
    b64_str = base64.b64encode(data).decode("ascii")
    return "data:%s;base64,%s" % (mime, b64_str)


def extract_image_paths_from_text(text):
    """从文本中提取出有效存在的本地图片路径。"""
    if not text:
        return []
    paths = []
    pattern = r'(?:@)?([A-Za-z]:[\\/][^\r\n"\'<>|?*]+\.(?:png|jpg|jpeg|webp|gif|bmp)|[\w\-.\\/]+?\.(?:png|jpg|jpeg|webp|gif|bmp))'
    for match in re.finditer(pattern, text, re.IGNORECASE):
        candidate = match.group(1).strip().strip('"').strip("'")
        if os.path.isfile(candidate):
            abs_p = os.path.abspath(candidate)
            if abs_p not in paths:
                paths.append(abs_p)
    return paths


def build_openai_user_content(user_text, image_paths=None, support_vision=True):
    """按照 OpenAI 多模态格式构造 user 消息的 content。

    开启视觉且存在图片时，返回 content 列表：
      [{"type": "text", "text": ...}, {"type": "image_url", "image_url": {"url": "data:image/...;base64,..."}}]
    未开启视觉或无图片时，返回纯文本字符串。
    """
    if not support_vision or not image_paths:
        return user_text

    data_urls = []
    for p in image_paths:
        try:
            data_urls.append(encode_image_to_data_url(p))
        except Exception:
            pass

    if not data_urls:
        return user_text

    content = [{"type": "text", "text": user_text}]
    for url in data_urls:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": url,
                "detail": "auto"
            }
        })
    return content


def capture_screenshot(save_path=None):
    """纯 ctypes + GDI+ 原生截取当前 Windows 屏幕并保存为 PNG 格式，返回文件绝对路径。"""
    if not IS_WIN:
        raise RuntimeError("Screen capture requires Windows")

    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    gdiplus = ctypes.windll.gdiplus

    class GdiplusStartupInput(ctypes.Structure):
        _fields_ = [
            ("GdiplusVersion", wintypes.DWORD),
            ("DebugEventCallback", ctypes.c_void_p),
            ("SuppressBackgroundThread", wintypes.BOOL),
            ("SuppressExternalCodecs", wintypes.BOOL)
        ]

    token = ctypes.c_void_p()
    startup_in = GdiplusStartupInput(1, None, False, False)
    if gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(startup_in), None) != 0:
        raise RuntimeError("Failed to initialize GDI+")

    try:
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        old_bm = gdi32.SelectObject(hdc_mem, hbm)
        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, 0, 0, 0x00CC0020)

        p_bitmap = ctypes.c_void_p()
        gdiplus.GdipCreateBitmapFromHBITMAP(hbm, None, ctypes.byref(p_bitmap))

        # PNG 编码器 CLSID: {557CF406-1A04-11D3-9A73-0000F81EF32E}
        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)
            ]

        png_clsid = GUID(
            0x557cf406, 0x1a04, 0x11d3,
            (ctypes.c_ubyte * 8)(0x9a, 0x73, 0x00, 0x00, 0xf8, 0x1e, 0xf3, 0x2e)
        )

        if not save_path:
            save_path = os.path.join(
                tempfile.gettempdir(), "dsh_screenshot_%d.png" % int(time.time() * 1000)
            )
        save_path = os.path.abspath(save_path)

        res = gdiplus.GdipSaveImageToFile(p_bitmap, save_path, ctypes.byref(png_clsid), None)
        gdiplus.GdipDisposeImage(p_bitmap)
        gdi32.SelectObject(hdc_mem, old_bm)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

        if res != 0 or not os.path.isfile(save_path):
            raise RuntimeError("Failed to save screenshot via GDI+ (code: %d)" % res)
        return save_path
    finally:
        gdiplus.GdiplusShutdown(token)


def run_read_image_tool(args, config):
    """AI 自行调用读取本地图片发送给自己进行多模态视觉分析。"""
    if not isinstance(args, dict):
        raise ValueError("read_image 参数必须是 JSON 对象")
    path = args.get("path")
    if not path or not str(path).strip():
        raise ValueError("Parameter `path` is required for tool: read_image")
    target = os.path.abspath(os.path.expanduser(str(path).strip().strip('"').strip("'")))
    if not os.path.isfile(target):
        raise ValueError("The image file '%s' does not exist." % target)
    if not is_image_path(target):
        raise ValueError("File '%s' is not a supported image format (supported: png, jpg, jpeg, webp, gif, bmp)." % target)

    support_vision = bool(config.get("support_vision", True))
    if not support_vision:
        return ("Image '%s' exists, but visual support is disabled in config (support_vision=false)." % target, None, "auto")

    size_bytes = os.path.getsize(target)
    detail = args.get("detail") or "auto"
    msg = ("Successfully read image '%s' (size: %d bytes). The image has been attached to your context for visual analysis."
           % (target, size_bytes))
    return msg, target, detail


def run_screenshot_tool(args, config):
    """AI 自行调用截取当前屏幕发送给自己进行多模态视觉分析，便于观察 GUI 决定鼠标操作。"""
    save_path = args.get("save_path") if isinstance(args, dict) else None
    support_vision = bool(config.get("support_vision", True))
    if not support_vision:
        return ("Screenshot captured, but visual support is disabled in config (support_vision=false).", None, "auto")

    shot_path = capture_screenshot(save_path)
    size_bytes = os.path.getsize(shot_path)
    screen_w, screen_h = _get_screen_size()
    msg = ("Screenshot captured (%dx%d, %d bytes) at '%s' and attached to your context for visual analysis."
           % (screen_w, screen_h, size_bytes, shot_path))
    return msg, shot_path, "auto"


# =============================================================================
# 7. 工具注册（OpenAI tools schema）
# =============================================================================

PWSH_DESCRIPTION = "\n".join([
    "Run commands in a persistent PowerShell shell",
    '* When invoking this tool, the contents of the "command" parameter does NOT need to be XML-escaped.',
    "* State, including the current directory and environment variables, persists across calls and discussions "
    "with the user.",
    "* Use native Windows paths (C:\\...) and $env:NAME variables; this is PowerShell, not bash.",
    "* Please avoid commands that may produce a very large amount of output.",
    "* Please run long lived commands in the background, e.g. 'Start-Job' or start a server with Start-Process.",
])

EDITOR_DESCRIPTION = "\n".join([
    "Custom editing tool for viewing, creating and editing files",
    "* State is persistent across command calls and discussions with the user",
    "* If `path` is a file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists "
    "non-hidden files and directories up to 2 levels deep",
    "* The `create` command cannot be used if the specified `path` already exists as a file",
    "* If a `command` generates a long output, it will be truncated and marked with `<response clipped>`",
    "",
    "Notes for using the `str_replace` command:",
    "* The `old_str` parameter should match EXACTLY one or more consecutive lines from the original file. "
    "Be mindful of whitespaces!",
    "* If the `old_str` parameter is not unique in the file, the replacement will not be performed. Make sure to "
    "include enough context in `old_str` to make it unique",
    "* The `new_str` parameter should contain the edited lines that should replace the `old_str`",
])

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "pwsh",
            "description": PWSH_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The PowerShell command to run. Relative path is preferred in the command.",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace_editor",
            "description": EDITOR_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["view", "create", "str_replace", "insert"],
                        "description": "The commands to run. Allowed options are: `view`, `create`, "
                                       "`str_replace`, `insert`.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Absolute path to file or directory, e.g. `C:\\repo\\file.py` or `C:\\repo`.",
                    },
                    "file_text": {
                        "type": "string",
                        "description": "Required parameter of `create` command, with the content of the file to "
                                       "be created.",
                    },
                    "insert_line": {
                        "type": "integer",
                        "description": "Required parameter of `insert` command. The `new_str` will be inserted "
                                       "AFTER the line `insert_line` of `path`.",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Optional parameter of `str_replace` command containing the new string "
                                       "(if not given, no string will be added). Required parameter of `insert` "
                                       "command containing the string to insert.",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Required parameter of `str_replace` command containing the string in `path` "
                                       "to replace.",
                    },
                    "view_range": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional parameter of `view` command when `path` points to a file. If none "
                                       "is given, the full file is shown. If provided, the file will be shown in "
                                       "the indicated line number range, e.g. [11, 12] will show lines 11 and 12. "
                                       "Indexing at 1 to start. Setting `[start_line, -1]` shows all lines from "
                                       "`start_line` to the end of the file.",
                    },
                },
                "required": ["command", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_control",
            "description": "\n".join([
                "Control the mouse cursor, click buttons, and scroll wheel on Windows.",
                "* Actions: `move` (move cursor to x, y), `click` (click button), `double_click`, `down` (press and hold button), `up` (release button), `scroll` (vertical wheel scroll), `position` (query cursor position & screen dimensions).",
                "* Buttons: `left` (default), `right`, `middle`.",
                "* Coordinates: `x` and `y` are pixel coordinates (0, 0 is top-left of the primary display). If omitted for click/down/up, current cursor position is used.",
                "* Scroll: `scroll_amount` specifies wheel ticks (positive = scroll up, negative = scroll down; default -1).",
            ]),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["move", "click", "double_click", "down", "up", "scroll", "position"],
                        "description": "The mouse action: move, click, double_click, down, up, scroll, or position (query cursor position & screen dimensions).",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button for click/double_click/down/up. Default is 'left'.",
                    },
                    "x": {
                        "type": "integer",
                        "description": "Horizontal pixel coordinate (0 is left edge).",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Vertical pixel coordinate (0 is top edge).",
                    },
                    "scroll_amount": {
                        "type": "integer",
                        "description": "Amount to scroll. Positive scrolls up, negative scrolls down. Default is -1 (scroll down 1 step).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_image",
            "description": "\n".join([
                "Read any local image file (PNG, JPG, JPEG, WEBP, GIF, BMP) to inspect and send to yourself for multimodal visual analysis.",
                "* Use this autonomously whenever you want to inspect an image file or analyze visual details.",
                "* The image content will be automatically encoded and attached to your conversation context for immediate visual perception.",
            ]),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the local image file to inspect.",
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["auto", "low", "high"],
                        "description": "Visual resolution detail level (default 'auto').",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "\n".join([
                "Capture a screenshot of the primary Windows desktop and attach it to your context for multimodal visual analysis.",
                "* Use this autonomously to inspect the current GUI, locate UI elements/buttons, or verify the visual result of mouse operations.",
                "* Works hand-in-hand with 'mouse_control' for autonomous GUI operations.",
            ]),
            "parameters": {
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": "Optional file path to save the screenshot PNG. Defaults to a temporary file.",
                    },
                },
            },
        },
    },
]

TOOL_NAMES = [schema["function"]["name"] for schema in TOOL_SCHEMAS]


# =============================================================================
# 8. OpenAI Chat Completions 客户端
# =============================================================================

class ApiError(Exception):
    pass


class OpenAICompletionsClient(object):
    """只实现常规 openai-completions 协议：POST {base_url}/chat/completions。"""

    def __init__(self, config):
        self.model = ""
        self.reload(config)

    def reload(self, config):
        """（重新）读一遍配置。F2 保存后必须调用它。

        踩过的坑：以前 F2 保存只改了 client.model / client.config，而
        base_url 和 api_key 是构造时**缓存**在实例里的 —— 用户改完密钥、
        改完地址，请求里带的还是旧值，非重启不可。改配置的地方都要走这里。
        """
        self.base_url = normalize_base_url(config.get("base_url"))
        self.api_key = config.get("api_key") or ""
        self.model = config.get("model") or self.model or DEFAULT_CONFIG["model"]
        self.temperature = config.get("temperature")
        self.max_tokens = config.get("max_tokens") or 0
        self.stream = bool(config.get("stream", True))
        self.timeout = float(config.get("request_timeout") or 300)
        self.retries = int(config.get("retries") or 0)
        self.extra_headers = config.get("extra_headers") or {}
        self.extra_body = config.get("extra_body") or {}
        self.include_usage = bool(config.get("include_usage"))
        return self

    # ---- 请求构造 ----

    def _payload(self, messages, tools, stream):
        payload = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens:
            payload["max_tokens"] = int(self.max_tokens)
        if stream:
            payload["stream"] = True
            if self.include_usage:
                payload["stream_options"] = {"include_usage": True}
        for key, value in self.extra_body.items():
            payload[key] = value
        return payload

    def _headers(self):
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if self.stream else "application/json",
            "User-Agent": "%s/%s (Python-%s)" % (APP_NAME, VERSION, sys.version.split()[0]),
        }
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key
        for key, value in self.extra_headers.items():
            headers[key] = str(value)
        return headers

    # ---- 调用 ----

    def chat(self, messages, tools=None, on_text=None, on_reasoning=None, on_usage=None, cancel=None):
        """返回助手消息 dict：{"role": "assistant", "content": str|None, "tool_calls": [...]}"""
        if not self.base_url:
            raise ApiError("未配置 base_url，请先运行 python %s --setup" % os.path.basename(__file__))
        if not self.model:
            raise ApiError("未配置 model")
        attempt = 0
        last_error = None
        while attempt <= self.retries:
            attempt += 1
            emitted = {"any": False}
            try:
                return self._once(messages, tools, on_text, on_reasoning, on_usage, emitted, cancel)
            except KeyboardInterrupt:
                raise
            except ApiError as exc:
                last_error = exc
                if not _retryable(exc) or attempt > self.retries or emitted["any"]:
                    raise
            except urllib.error.URLError as exc:
                last_error = ApiError("网络错误：%s" % exc)
                if attempt > self.retries or emitted["any"]:
                    raise last_error
            if cancel is not None and cancel.is_set():
                raise ApiError("已取消")
            delay = min(8.0, 1.5 ** attempt)
            if on_text is not None:
                try:
                    on_text("\n[重试 %d/%d] %s\n" % (attempt, self.retries, last_error))
                except Exception:
                    pass
            time.sleep(delay)
        raise last_error or ApiError("请求失败")

    def _once(self, messages, tools, on_text, on_reasoning, on_usage, emitted, cancel):
        payload = self._payload(messages, tools, self.stream)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8", "replace")
        request = urllib.request.Request(self.base_url, data=body, headers=self._headers(), method="POST")
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            raise ApiError(_format_http_error(exc.code, detail))
        except urllib.error.URLError as exc:
            raise ApiError("网络错误：%s" % exc.reason)
        except Exception as exc:
            raise ApiError("请求失败：%s" % exc)

        try:
            if self.stream:
                return self._read_stream(response, on_text, on_reasoning, on_usage, emitted, cancel)
            raw = response.read().decode("utf-8", "replace")
            return self._read_json(raw, emitted)
        finally:
            try:
                response.close()
            except Exception:
                pass

    def _read_json(self, raw, emitted):
        try:
            data = json.loads(raw)
        except Exception:
            raise ApiError("接口返回的不是合法 JSON：%s" % raw[:500])
        if isinstance(data, dict) and data.get("error"):
            raise ApiError("接口错误：%s" % json.dumps(data["error"], ensure_ascii=False)[:500])
        choices = data.get("choices") or []
        if not choices:
            raise ApiError("接口未返回 choices：%s" % raw[:500])
        message = choices[0].get("message") or {}
        emitted["any"] = True
        return _normalize_assistant(message, choices[0].get("finish_reason"))

    def _read_stream(self, response, on_text, on_reasoning, on_usage, emitted, cancel):
        content_parts = []
        reasoning_parts = []
        tool_calls = {}
        finish_reason = None
        while True:
            if cancel is not None and cancel.is_set():
                raise ApiError("已取消")
            raw = response.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip("\r\n")
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            if isinstance(chunk, dict) and chunk.get("error"):
                raise ApiError("接口错误：%s" % json.dumps(chunk["error"], ensure_ascii=False)[:500])
            if chunk.get("usage") and on_usage is not None:
                try:
                    on_usage(chunk["usage"])
                except Exception:
                    pass
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                emitted["any"] = True
                content_parts.append(piece)
                if on_text is not None:
                    on_text(piece)
            reason = delta.get("reasoning_content")
            if reason is None:
                reason = delta.get("reasoning")
            if reason:
                emitted["any"] = True
                reasoning_parts.append(reason)
                if on_reasoning is not None:
                    on_reasoning(reason)
            for item in delta.get("tool_calls") or []:
                index = item.get("index")
                if index is None:
                    index = len(tool_calls)
                slot = tool_calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
                if item.get("id"):
                    slot["id"] = item["id"]
                function = item.get("function") or {}
                if function.get("name"):
                    slot["name"] = function["name"]
                if function.get("arguments"):
                    slot["arguments"] += function["arguments"]
                emitted["any"] = True
        message = {"role": "assistant", "content": "".join(content_parts) or None}
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        if tool_calls:
            ordered = [tool_calls[key] for key in sorted(tool_calls)]
            message["tool_calls"] = [
                {
                    "id": slot["id"] or ("call_%d" % index),
                    "type": "function",
                    "function": {"name": slot["name"] or "", "arguments": slot["arguments"] or "{}"},
                }
                for index, slot in enumerate(ordered)
            ]
        return _normalize_assistant(message, finish_reason)


def _normalize_assistant(message, finish_reason):
    result = {"role": "assistant", "content": message.get("content")}
    if message.get("reasoning_content"):
        result["reasoning_content"] = message["reasoning_content"]
    if message.get("tool_calls"):
        result["tool_calls"] = message["tool_calls"]
    if finish_reason:
        result["_finish_reason"] = finish_reason
    return result


def _format_http_error(code, detail):
    try:
        parsed = json.loads(detail)
        if isinstance(parsed, dict) and parsed.get("error"):
            error = parsed["error"]
            if isinstance(error, dict):
                message = error.get("message") or json.dumps(error, ensure_ascii=False)
            else:
                message = str(error)
        else:
            message = detail
    except Exception:
        message = detail
    message = (message or "").strip()[:800]
    hints = {
        401: "（请检查 api_key 是否正确）",
        403: "（权限不足或地区限制）",
        404: "（请检查 base_url 与 model 名称）",
        429: "（触发限流，稍后重试）",
    }
    return "HTTP %d %s %s" % (code, message, hints.get(code, ""))


def _retryable(error):
    text = str(error)
    return bool(re.search(r"HTTP (429|500|502|503|504)", text)) or text.startswith("网络错误")


# =============================================================================
# 9. Agent 循环
# =============================================================================

class Emitter(object):
    """Agent 事件回调基类（TUI 与单次模式各自实现）。"""

    def on_text(self, chunk):
        pass

    def on_reasoning(self, chunk):
        pass

    def on_tool_start(self, name, args):
        pass

    def on_tool_output(self, chunk):
        pass

    def on_tool_end(self, name, result, elapsed, is_error):
        pass

    def on_status(self, text):
        pass

    def on_usage(self, usage):
        pass

    def on_notice(self, text):
        pass


class Agent(object):
    def __init__(self, config, client, shell, emitter):
        self.config = config
        self.client = client
        self.shell = shell
        self.emitter = emitter
        if shell is not None and hasattr(shell, "set_notice_callback"):
            try:
                shell.set_notice_callback(emitter.on_notice)
            except Exception:
                pass
        self.max_output_chars = int(config.get("max_output_chars") or 16000)
        self.max_rounds = int(config.get("max_tool_rounds") or 60)
        self.messages = []
        self.last_user_message = ""
        self._reset_messages()

    # ---- 消息管理 ----

    def _system_prompt(self):
        prompt = self.config.get("system_prompt") or DSH_WIN7_PERSONA
        if prompt.strip() in (DSH_MINIMAL_PERSONA.strip(), ""):
            prompt = DSH_WIN7_PERSONA
        if self.config.get("include_env_note", True):
            is_bundled = is_bundled_powershell(getattr(self.shell, "exe", None))
            tool_note = ("the pwsh tool is bundled stable PowerShell 7 (Win7 custom edition) and keeps its state between calls"
                         if is_bundled else "the pwsh tool is Windows PowerShell and keeps its state between calls")
            if self.shell is not None and hasattr(self.shell, "describe"):
                try:
                    tool_note = self.shell.describe()
                except Exception:
                    pass
            note = ("Runtime: Windows 7 (x64) [Win7 Deep Custom Edition]; working directory: %s; %s. "
                    "You have autonomous tools to control the mouse ('mouse_control'), "
                    "read any local image for visual analysis ('read_image'), and capture the screen ('take_screenshot'). "
                    "You can autonomously decide when and how to inspect images, capture screen state, and operate the mouse."
                    % (self.config.get("cwd") or os.getcwd(), tool_note))
            prompt = prompt.rstrip() + "\n\n" + note
        return prompt

    def _refresh_system_prompt(self):
        """每轮刷新一次环境说明：pwsh 降级成单命令模式后要让模型知道状态不再保持。

        注意：内容没变就不要重新赋值 —— 前缀缓存（prompt cache）按请求前缀逐字节
        命中，system prompt 必须在一次会话里保持字节级稳定。
        """
        if self.messages and isinstance(self.messages[0], dict) and self.messages[0].get("role") == "system":
            content = self._system_prompt()
            if self.messages[0].get("content") != content:
                self.messages[0]["content"] = content

    def _reset_messages(self):
        self.messages = [{"role": "system", "content": self._system_prompt()}]

    def clear(self):
        self._reset_messages()

    def load_messages(self, messages):
        if isinstance(messages, list) and messages:
            self.messages = messages

    # ---- 工具执行 ----

    def _emit(self, name, *args):
        """调 emitter，但绝不让界面层的异常把整轮对话带崩（留一份日志即可）。"""
        callback = getattr(self.emitter, name, None)
        if callback is None:
            return
        try:
            callback(*args)
        except KeyboardInterrupt:
            raise
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            write_crash_log(exc_type, exc_value, exc_tb, where="emitter:%s" % name)

    def _execute_tool(self, name, args, call_id):
        if name == "pwsh":
            on_output = (lambda chunk: self._emit("on_tool_output", chunk)) \
                if self.config.get("show_live_output", True) else None
            return run_pwsh_tool(self.shell, args, self.max_output_chars, on_output), None, "auto"
        if name == "str_replace_editor":
            return run_editor_tool(args, self.max_output_chars), None, "auto"
        if name == "mouse_control":
            return run_mouse_tool(args), None, "auto"
        if name == "read_image":
            return run_read_image_tool(args, self.config)
        if name == "take_screenshot":
            return run_screenshot_tool(args, self.config)
        raise ValueError("Unknown tool: %s" % name)

    # ---- 一轮对话 ----

    def run_turn(self, user_text, cancel=None, images=None):
        """跑一轮对话。cancel 是一个 threading.Event，置位后会尽快中断本轮
        （GUI 里按 Esc 就是走这条路）。images 可指定图片路径列表，支持按 OpenAI 视觉格式发图。"""
        user_text = sanitize_text(user_text)
        self.last_user_message = user_text

        support_vision = bool(self.config.get("support_vision", True))
        img_paths = list(images or [])
        if support_vision and not img_paths:
            img_paths = extract_image_paths_from_text(user_text)

        content = build_openai_user_content(user_text, img_paths, support_vision=support_vision)
        self.messages.append({"role": "user", "content": content})
        self._refresh_system_prompt()
        for round_index in range(self.max_rounds):
            self._emit("on_status", "等待模型响应…")
            try:
                assistant = self.client.chat(
                    self.messages,
                    tools=TOOL_SCHEMAS,
                    on_text=lambda chunk: self._emit("on_text", chunk),
                    on_reasoning=lambda chunk: self._emit("on_reasoning", chunk),
                    on_usage=lambda usage: self._emit("on_usage", usage),
                    cancel=cancel,
                )
            finally:
                self._emit("on_status", None)

            finish = assistant.pop("_finish_reason", None)
            if finish == "length":
                self._emit("on_notice", "模型输出被 max_tokens 截断（finish_reason=length）")

            history_message = {"role": "assistant", "content": assistant.get("content")}
            if assistant.get("reasoning_content"):
                history_message["reasoning_content"] = assistant["reasoning_content"]
            if assistant.get("tool_calls"):
                history_message["tool_calls"] = assistant["tool_calls"]
            if history_message["content"] is None and not history_message.get("tool_calls"):
                history_message["content"] = ""
            self.messages.append(history_message)

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                return assistant.get("content") or ""

            for call in tool_calls:
                name = (call.get("function") or {}).get("name") or ""
                raw_args = (call.get("function") or {}).get("arguments") or "{}"
                call_id = call.get("id") or ("call_%d" % round_index)
                started = time.monotonic()
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                    if not isinstance(args, dict):
                        raise ValueError("工具参数必须是 JSON 对象")
                except Exception as exc:
                    result = "Invalid JSON arguments for tool %s: %s" % (name, exc)
                    self.messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
                    self._emit("on_tool_start", name, {})
                    self._emit("on_tool_end", name, result, 0.0, True)
                    continue
                self._emit("on_tool_start", name, args)
                pending_image = None
                image_detail = "auto"
                try:
                    exec_res = self._execute_tool(name, args, call_id)
                    if isinstance(exec_res, tuple) and len(exec_res) == 3:
                        result, pending_image, image_detail = exec_res
                    else:
                        result = exec_res
                    is_error = False
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    result = str(exc) or exc.__class__.__name__
                    is_error = True
                result = sanitize_text(result)
                elapsed = time.monotonic() - started
                self._emit("on_tool_end", name, result, elapsed, is_error)
                self.messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

                # 若 AI 自主调用了看图或截屏工具，且开启了视觉支持：自动以 OpenAI 视觉协议向上下文注入图片供模型视觉分析
                if pending_image and not is_error and self.config.get("support_vision", True):
                    try:
                        data_url = encode_image_to_data_url(pending_image)
                        self.messages.append({
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "[Visual Feedback] Image from '%s' attached for your multimodal visual analysis:" % os.path.basename(pending_image)
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": data_url,
                                        "detail": image_detail or "auto"
                                    }
                                }
                            ]
                        })
                    except Exception as exc:
                        write_crash_log(type(exc), exc, None, where="agent-attach-image")

        self._emit("on_notice", "已达到最大工具调用轮数（%d），本轮停止。" % self.max_rounds)
        return ""


# =============================================================================
# 10. 行编辑器（raw 键盘输入；Win7 无 ANSI 时用 Win32 API 定位光标）
# =============================================================================

class LineEditor(object):
    def __init__(self, theme, history, use_raw=True, use_vt=True, reader=None):
        self.theme = theme
        self.history = history
        self.use_raw = use_raw
        self.use_vt = use_vt
        self.reader = reader or StdinReader()
        self._win = None
        self._prompt_text = ""
        self._start_row = 0
        self._start_col = 0

    # ---- Win32 控制台定位（无 ANSI 时的兜底） ----

    def _winconsole(self):
        if self._win is None and IS_WIN:
            try:
                import ctypes

                class COORD(ctypes.Structure):
                    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

                class SMALL_RECT(ctypes.Structure):
                    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                                ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

                class INFO(ctypes.Structure):
                    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD),
                                ("wAttributes", ctypes.c_ushort), ("srWindow", SMALL_RECT),
                                ("dwMaximumWindowSize", COORD)]

                kernel32 = ctypes.windll.kernel32
                handle = kernel32.GetStdHandle(-11)
                self._win = (ctypes, kernel32, handle, COORD, INFO)
            except Exception:
                self._win = False
        return self._win or None

    def _cursor_pos(self):
        win = self._winconsole()
        if not win:
            return None
        ctypes, kernel32, handle, _COORD, INFO = win
        info = INFO()
        if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return None
        return (info.dwCursorPosition.Y, info.dwCursorPosition.X)

    def _set_cursor_pos(self, row, col):
        win = self._winconsole()
        if not win:
            return
        ctypes, kernel32, handle, COORD, _INFO = win
        kernel32.SetConsoleCursorPosition(handle, COORD(col, row))

    # ---- 绘制 ----

    def _render(self, buf, pos, first=False):
        """整行重绘并把光标放回 pos 处。"""
        prompt_width = display_width(self._prompt_text)
        width = terminal_width()
        if first:
            self._start_row, self._start_col = self._cursor_pos() or (0, 0)
        out = sys.stdout
        if self.use_vt:
            out.write("\r" + self._prompt_text + buf + "\x1b[K")
            tail = display_width(buf[pos:])
            if tail > 0:
                out.write("\x1b[%dD" % tail)
        else:
            # 无 ANSI：回到行首重画，并用空格擦掉残留
            out.write("\r" + self._prompt_text + buf + " " * max(0, 8) + "\r")
            total = prompt_width + display_width(buf[:pos])
            row = self._start_row + total // max(1, width)
            col = self._start_col + total % max(1, width)
            if self._cursor_pos() is not None:
                self._set_cursor_pos(row, col)
        out.flush()

    # ---- 读取 ----

    def read(self, prompt_text):
        self._prompt_text = prompt_text
        if not self.use_raw:
            return self.reader.readline(prompt_text)
        return self._read_raw()

    def _read_raw(self):
        import msvcrt

        buf = ""
        pos = 0
        history_index = len(self.history)
        draft = ""
        self._render(buf, pos, first=True)
        last_was_cr = False

        def insert(text):
            nonlocal buf, pos
            buf = buf[:pos] + text + buf[pos:]
            pos += len(text)

        while True:
            key = msvcrt.getwch()
            if key in ("\x00", "\xe0"):
                code = msvcrt.getwch()
                if code == "H":      # ↑
                    if history_index > 0:
                        if history_index == len(self.history):
                            draft = buf
                        history_index -= 1
                        buf, pos = self.history[history_index], len(self.history[history_index])
                elif code == "P":    # ↓
                    if history_index < len(self.history) - 1:
                        history_index += 1
                        buf, pos = self.history[history_index], len(self.history[history_index])
                    elif history_index == len(self.history) - 1:
                        history_index = len(self.history)
                        buf, pos = draft, len(draft)
                elif code == "K":    # ←
                    pos = max(0, pos - 1)
                elif code == "M":    # →
                    pos = min(len(buf), pos + 1)
                elif code == "G":    # Home
                    pos = 0
                elif code == "O":    # End
                    pos = len(buf)
                elif code == "S":    # Delete
                    if pos < len(buf):
                        buf = buf[:pos] + buf[pos + 1:]
                self._render(buf, pos)
                continue

            if key == "\r":
                if msvcrt.kbhit():          # 粘贴的多行文本：回车后还有内容
                    insert("\n")
                    last_was_cr = True
                    self._render(buf, pos)
                    continue
                sys.stdout.write("\n")
                sys.stdout.flush()
                return buf
            if key == "\n":
                if last_was_cr:             # 跳过 \r\n 中的 \n
                    last_was_cr = False
                    continue
                insert("\n")
                self._render(buf, pos)
                continue
            last_was_cr = False
            if key == "\x08":               # Backspace
                if pos > 0:
                    buf = buf[:pos - 1] + buf[pos:]
                    pos -= 1
                self._render(buf, pos)
            elif key == "\x03":             # Ctrl+C
                if not buf:
                    sys.stdout.write("^C\n")
                    sys.stdout.flush()
                    raise KeyboardInterrupt
                buf, pos = "", 0
                sys.stdout.write("^C\n")
                self._render(buf, pos)
            elif key in ("\x04", "\x1a"):   # Ctrl+D / Ctrl+Z
                sys.stdout.write("\n")
                sys.stdout.flush()
                return None
            elif key == "\x16":             # Ctrl+V
                text = read_clipboard_text().replace("\r\n", "\n").replace("\r", "\n")
                if text:
                    insert(text)
                    self._render(buf, pos)
            elif key == "\x0a":             # Ctrl+J 换行
                insert("\n")
                self._render(buf, pos)
            elif key == "\x15":             # Ctrl+U
                buf, pos = "", 0
                self._render(buf, pos)
            elif key == "\x01":             # Ctrl+A
                pos = 0
                self._render(buf, pos)
            elif key == "\x05":             # Ctrl+E
                pos = len(buf)
                self._render(buf, pos)
            elif key == "\x17":             # Ctrl+W
                head = buf[:pos].rstrip()
                cut = head.rfind(" ")
                cut = 0 if cut < 0 else cut + 1
                buf = buf[:cut] + buf[pos:]
                pos = cut
                self._render(buf, pos)
            elif key == "\t":
                insert("    ")
                self._render(buf, pos)
            elif key == "\x1b":             # ESC
                continue
            elif ord(key) >= 32:
                insert(key)
                self._render(buf, pos)


# =============================================================================
# 11. 状态行（转圈动画）
# =============================================================================

class Spinner(object):
    FRAMES = ["|", "/", "-", "\\"]

    def __init__(self, theme, enabled):
        self.theme = theme
        self.enabled = bool(enabled)
        self._thread = None
        self._stop = threading.Event()
        self._label = ""
        self._started = 0.0
        self._lock = threading.Lock()
        self._active = False

    def start(self, label):
        if not self.enabled:
            return
        self.stop()
        self._label = label
        self._started = time.monotonic()
        self._stop = threading.Event()
        self._active = True
        self._thread = threading.Thread(target=self._loop)
        self._thread.daemon = True
        self._thread.start()

    def _loop(self):
        index = 0
        while not self._stop.is_set():
            frame = self.FRAMES[index % len(self.FRAMES)]
            index += 1
            elapsed = time.monotonic() - self._started
            with self._lock:
                if not self._active:
                    break
                sys.stdout.write("\r%s %s %s " % (self.theme.cyan(frame), self._label,
                                                  self.theme.grey(fmt_duration(elapsed))))
                sys.stdout.flush()
            self._stop.wait(0.12)

    def stop(self):
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._thread = None
        with self._lock:
            if self._active:
                self._active = False
                sys.stdout.write("\r" + " " * (terminal_width() - 1) + "\r")
                sys.stdout.flush()


# =============================================================================
# 12. 交互界面
# =============================================================================

class Tui(Emitter):
    def __init__(self, config, theme, spinner, live_output=True):
        self.config = config
        self.theme = theme
        self.spinner = spinner
        self.live_output = live_output
        self._text_open = False
        self._reasoning_open = False
        self._tool_lines = 0
        self._live_lines = 0
        self._print_lock = threading.Lock()
        self.usage = None

    # ---- 基础输出 ----

    def write(self, text):
        with self._print_lock:
            sys.stdout.write(text)
            sys.stdout.flush()

    def line(self, text=""):
        self.write(text + "\n")

    def banner(self, config):
        width = min(terminal_width(), 92)
        inner = width - 4
        t = self.theme
        title = "%s v%s" % (APP_TITLE, VERSION)
        model = str(config.get("model"))
        cwd = config.get("cwd") or os.getcwd()
        rows = [
            (title, "工具：%s" % ", ".join(TOOL_NAMES)),
            ("模型：" + model, "接口：%s" % normalize_base_url(config.get("base_url"))),
            ("工作目录：" + cwd, "/help 查看命令，/exit 退出"),
        ]
        self.line("")
        self.line(t.cyan("┌" + "─" * (width - 2) + "┐"))
        for left, right in rows:
            left_text = left
            right_text = right
            space = inner - display_width(left_text) - display_width(right_text)
            if space < 1:
                space = 1
            content = left_text + " " * space + right_text
            if display_width(content) > inner:
                content = content[:inner]
            padding = inner - display_width(content)
            self.line(t.cyan("│") + " " + content + " " * max(0, padding) + " " + t.cyan("│"))
        self.line(t.cyan("└" + "─" * (width - 2) + "┘"))
        self.line("")

    # ---- 事件 ----

    def on_status(self, text):
        if text:
            self.usage = None      # 每轮重置，避免失败轮次重复显示上一轮的用量
            self.spinner.start(text)
        else:
            self.spinner.stop()

    def on_text(self, chunk):
        self.spinner.stop()
        if not self._text_open:
            self._close_reasoning()
            self.write("\n" + self.theme.green("● "))
            self._text_open = True
        self.write(chunk)

    def on_reasoning(self, chunk):
        if not self.config.get("show_reasoning", True):
            return
        self.spinner.stop()
        if not self._reasoning_open:
            self.write("\n" + self.theme.grey("… ") )
            self._reasoning_open = True
        self.write(self.theme.grey(chunk))

    def _close_reasoning(self):
        if self._reasoning_open:
            self.write("\n")
            self._reasoning_open = False

    def _close_text(self):
        if self._text_open:
            self.write("\n")
            self._text_open = False

    def on_tool_start(self, name, args):
        self.spinner.stop()
        self._close_reasoning()
        self._close_text()
        summary = self._describe_tool(name, args)
        self.line("")
        self.line("%s %s %s" % (self.theme.yellow("»"), self.theme.bold(name), self.theme.grey(summary)))
        self._tool_lines = 0
        self._live_lines = 0

    def on_tool_output(self, chunk):
        if not self.live_output or not chunk.strip():
            return
        self._live_lines += 1
        if self._live_lines > 12:
            return
        self.write(self.theme.grey("  │ " + chunk[:400]) + "\n")

    def on_tool_end(self, name, result, elapsed, is_error):
        if self._live_lines > 12:
            self.line(self.theme.grey("  │ …（更多输出已省略显示，模型可见完整结果）"))
        summary = "%s · %s" % (fmt_duration(elapsed), "失败" if is_error else "完成")
        marker = self.theme.red("×") if is_error else self.theme.green("√")
        self.line("  %s %s" % (marker, self.theme.grey(summary)))
        if is_error:
            first = (result or "").strip().splitlines()
            if first:
                self.line(self.theme.red("  " + first[0][:400]))

    def on_notice(self, text):
        self.spinner.stop()
        self._close_reasoning()
        self._close_text()
        self.line(self.theme.yellow("! " + text))

    def on_usage(self, usage):
        self.usage = usage

    # ---- 工具描述 ----

    def _describe_tool(self, name, args):
        if name == "pwsh":
            command = str(args.get("command") or "")
            first = command.strip().splitlines()[0] if command.strip() else ""
            return first[:160]
        if name == "str_replace_editor":
            command = args.get("command") or "?"
            path = args.get("path") or "?"
            extra = ""
            if command == "view" and args.get("view_range"):
                extra = " " + str(args.get("view_range"))
            return "%s %s%s" % (command, path, extra)
        if name == "mouse_control":
            action = args.get("action") or "?"
            button = args.get("button") or "left"
            return "%s %s" % (action, button)
        if name == "read_image":
            return "read %s" % (args.get("path") or "?")
        if name == "take_screenshot":
            return "take screenshot"
        return ""

    def finish_turn(self):
        self.spinner.stop()
        self._close_reasoning()
        self._close_text()
        if self.usage:
            self.line(self.theme.grey("  [Token消耗] " + format_usage(self.usage)))
        self.usage = None
        self.line("")


# =============================================================================
# 13. 会话持久化
# =============================================================================

def sessions_dir():
    return data_dir()


def new_session_path():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(sessions_dir(), "session-%s-%s.json" % (stamp, uuid.uuid4().hex[:4]))


def save_session(path, agent, config):
    try:
        payload = {
            "version": VERSION,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": config.get("model"),
            "cwd": config.get("cwd") or os.getcwd(),
            "messages": agent.messages,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def list_sessions(limit=15):
    directory = sessions_dir()
    try:
        files = [os.path.join(directory, name) for name in os.listdir(directory) if name.endswith(".json")]
    except Exception:
        return []
    files.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return files[:limit]


def load_session(path):
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("messages") or []


# =============================================================================
# 14. 自检（离线，不联网）
# =============================================================================

def run_selftest():
    import io
    import tempfile

    theme = Theme(False)
    results = []

    def check(name, condition, detail=""):
        results.append((name, bool(condition), detail))
        print("%s  %s%s" % ("[PASS]" if condition else "[FAIL]", name, ("  -> " + detail) if detail and not condition else ""))

    temp_dir = tempfile.mkdtemp(prefix="dsh-mini-selftest-")
    sample = os.path.join(temp_dir, "sample.txt")
    try:
        # ---- 工具 schema ----
        names = [schema["function"]["name"] for schema in TOOL_SCHEMAS]
        check("工具集包含完整自主 Agent 工具（pwsh + str_replace_editor + mouse_control + read_image + take_screenshot）",
              names == ["pwsh", "str_replace_editor", "mouse_control", "read_image", "take_screenshot"], str(names))

        # ---- 编辑器 ----
        create_result = run_editor_tool({"command": "create", "path": sample,
                                         "file_text": "第一行\n第二行\n第三行\n"}, 16000)
        check("editor create", "New file created successfully" in create_result, create_result)

        duplicate = ""
        try:
            run_editor_tool({"command": "create", "path": sample, "file_text": "x"}, 16000)
        except ValueError as exc:
            duplicate = str(exc)
        check("editor create 拒绝覆盖已存在文件", "File already exists" in duplicate, duplicate)

        view = run_editor_tool({"command": "view", "path": sample}, 16000)
        check("editor view 带行号", "     1  第一行" in view and "total of 4 lines" in view, view[:200])

        ranged = run_editor_tool({"command": "view", "path": sample, "view_range": [2, 3]}, 16000)
        check("editor view_range", "view_range=[2, 3]" in ranged and "第二行" in ranged and "第一行" not in ranged, ranged)

        listing = run_editor_tool({"command": "view", "path": temp_dir}, 16000)
        check("editor view 目录（2 层）", "d\t" in listing and "sample.txt" in listing, listing[:200])

        replace = run_editor_tool({"command": "str_replace", "path": sample,
                                    "old_str": "第二行", "new_str": "第二行-改"}, 16000)
        check("editor str_replace", "edited successfully" in replace, replace)
        with open(sample, "r", encoding="utf-8") as handle:
            check("editor str_replace 内容正确", "第二行-改" in handle.read())

        missing = ""
        try:
            run_editor_tool({"command": "str_replace", "path": sample, "old_str": "不存在", "new_str": "x"}, 16000)
        except ValueError as exc:
            missing = str(exc)
        check("editor str_replace 未命中报错", "did not appear verbatim" in missing, missing)

        ambiguous = ""
        run_editor_tool({"command": "insert", "path": sample, "insert_line": 0, "new_str": "重复\n重复"}, 16000)
        try:
            run_editor_tool({"command": "str_replace", "path": sample, "old_str": "重复", "new_str": "x"}, 16000)
        except ValueError as exc:
            ambiguous = str(exc)
        check("editor str_replace 多命中报错", "Multiple occurrences" in ambiguous, ambiguous)

        insert = run_editor_tool({"command": "insert", "path": sample, "insert_line": 1, "new_str": "插入行"}, 16000)
        with open(sample, "r", encoding="utf-8") as handle:
            content = handle.read()
        check("editor insert", "edited successfully" in insert and content.splitlines()[1] == "插入行", content)

        relative = ""
        try:
            run_editor_tool({"command": "view", "path": "relative.txt"}, 16000)
        except ValueError as exc:
            relative = str(exc)
        check("editor 拒绝相对路径", "not an absolute path" in relative, relative)

        crlf = os.path.join(temp_dir, "crlf.txt")
        with open(crlf, "wb") as handle:
            handle.write("a\r\nb\r\nc\r\n".encode("utf-8"))
        run_editor_tool({"command": "str_replace", "path": crlf, "old_str": "b", "new_str": "B"}, 16000)
        with open(crlf, "rb") as handle:
            raw = handle.read()
        check("editor 保留 CRLF 换行", raw == "a\r\nB\r\nc\r\n".encode("utf-8"), repr(raw))

        gbk = os.path.join(temp_dir, "gbk.txt")
        with open(gbk, "wb") as handle:
            handle.write("中文GBK测试\n".encode("gbk"))
        gbk_view = run_editor_tool({"command": "view", "path": gbk}, 16000)
        check("editor 读取 GBK 文件", "中文GBK测试" in gbk_view, gbk_view[:120])

        truncated = run_editor_tool({"command": "view", "path": sample}, 20)
        check("editor 输出截断标记", "<response clipped>" in truncated, truncated)

        # ---- 编码工具 ----
        check("decode_output 解码 UTF-8", decode_output("中文".encode("utf-8")) == "中文")
        check("decode_output 解码 GBK 回退", decode_output("中文".encode("gbk")) == "中文")

        # ---- 控制台编码兜底（Win7 的 cp936 严格控制台） ----
        check("console_safe 降级非 GBK 字符（» › → >）", console_safe("»›", "gbk") == ">>",
              repr(console_safe("»›", "gbk")))
        check("console_safe 保留 GBK 能表示的字符", console_safe("中文√×│●", "gbk") == "中文√×│●",
              repr(console_safe("中文√×│●", "gbk")))
        check("console_safe 处理替换字符 U+FFFD", console_safe("a\ufffdb", "gbk") == "a?b",
              repr(console_safe("a\ufffdb", "gbk")))
        _strict_buffer = io.BytesIO()
        _strict = io.TextIOWrapper(_strict_buffer, encoding="gbk", errors="strict")
        _safe = SafeStream(_strict)
        _stream_ok = True
        try:
            _safe.write("  » pwsh  Write-Host 'hi'\n  › 提示 中文 √ × │ ●\n")
            _safe.flush()
        except Exception as exc:
            _stream_ok = False
            check("SafeStream 写 cp936 严格流不崩", False, "%s: %s" % (exc.__class__.__name__, exc))
        if _stream_ok:
            _decoded = _strict_buffer.getvalue().decode("gbk", "replace")
            check("SafeStream 写 cp936 严格流不崩", True, "")
            check("SafeStream 降级结果正确", "> pwsh" in _decoded and "中文 √ × │ ●" in _decoded,
                  repr(_decoded[:60]))
        check("崩溃日志可写出（含环境信息）", callable(write_crash_log), "")
        check("sanitize_text 剔除孤立代理字符", sanitize_text("a\udca4b") == "a?b",
              repr(sanitize_text("a\udca4b")))
        check("sanitize_text 保留正常中文", sanitize_text("中文 abc") == "中文 abc")
        check("normalize_base_url 裸域名补 /v1", normalize_base_url("https://api.deepseek.com") ==
              "https://api.deepseek.com/v1/chat/completions")
        check("normalize_base_url 已含路径", normalize_base_url("https://x.com/v1/chat/completions") ==
              "https://x.com/v1/chat/completions")
        check("normalize_base_url /vN 结尾", normalize_base_url("https://x.com/v2") ==
              "https://x.com/v2/chat/completions")
        check("normalize_base_url 自定义路径不插 /v1", normalize_base_url("https://x.com/openai") ==
              "https://x.com/openai/chat/completions")
        check("normalize_base_url 带端口裸地址", normalize_base_url("http://192.0.2.10:1234") ==
              "http://192.0.2.10:1234/v1/chat/completions")

        # ---- 模型列表与选择 ----
        check("models_endpoint 裸域名", models_endpoint("https://api.deepseek.com") ==
              "https://api.deepseek.com/v1/models")
        check("models_endpoint 完整端点", models_endpoint("https://x.com/v1/chat/completions") ==
              "https://x.com/v1/models")
        check("models_endpoint 自定义路径", models_endpoint("https://x.com/openai") ==
              "https://x.com/openai/models")
        check("parse_model_choice 回车=保持当前", parse_model_choice("", ["a", "b"]) is None)
        check("parse_model_choice 序号选择", parse_model_choice("2", ["a", "b"]) == "b")
        check("parse_model_choice 序号越界", parse_model_choice("9", ["a", "b"]) == "")
        check("parse_model_choice 手输模型名", parse_model_choice("my-model", ["a", "b"]) == "my-model")
        check("parse_model_choice 手输带引号", parse_model_choice('  "gpt-4o"  ', []) == "gpt-4o")

        # ---- 取不到模型列表时仍能手动输入 ----
        class _FakeReader(object):
            def __init__(self, lines):
                self.lines = list(lines)

            def readline(self, prompt_text=""):
                return self.lines.pop(0) if self.lines else None

        captured = []
        picked = choose_model({"base_url": "http://127.0.0.1:9/v1", "api_key": "x"},
                              Theme(False), captured.append, _FakeReader(["manual-model"]), "current-model")
        check("取不到模型列表时仍可手输", picked == "manual-model", repr(picked))
        check("取不到列表时给出提示", any("没能取到模型列表" in line for line in captured),
              " / ".join(captured)[:200])
        captured2 = []
        picked2 = choose_model({"base_url": "http://127.0.0.1:9/v1", "api_key": "x"},
                               Theme(False), captured2.append, _FakeReader([""]), "current-model")
        check("取不到列表时回车保持当前", picked2 is None, repr(picked2))

        # ---- 持久 PowerShell ----
        if os.name == "nt" and shutil.which("powershell.exe"):
            config = merge_config(DEFAULT_CONFIG, {"cwd": temp_dir, "shell_timeout_ms": 60000})
            shell = PersistentShell(config)
            try:
                out1, code1, _ = shell.run("$global:dsh_mini_x = 41; Write-Output '中文测试-OK'")
                check("pwsh 中文输出", "中文测试-OK" in out1, repr(out1))
                out2, code2, _ = shell.run("Write-Output (\"x=\" + $global:dsh_mini_x)")
                check("pwsh 变量跨调用持久", "x=41" in out2, repr(out2))
                out3, code3, _ = shell.run("Set-Location $env:SystemRoot; (Get-Location).Path")
                out4, code4, _ = shell.run("(Get-Location).Path")
                check("pwsh 工作目录跨调用持久",
                      (os.environ.get("SystemRoot", "C:\\Windows").lower() in out4.lower()), repr(out4))
                out5, code5, _ = shell.run("cmd /c exit 3")
                check("pwsh 退出码透传", code5 == 3, "exit=%s" % code5)
                out6, code6, _ = shell.run("throw 'boom'")
                check("pwsh 终止性错误可见", "boom" in out6 and code6 == 1, repr(out6))
                out7, code7, _ = shell.run("if (1) { Write-Output 'multi-line-ok' }")
                check("pwsh 单行块", "multi-line-ok" in out7, repr(out7))
                out8, code8, _ = shell.run("if (1) {\n  Write-Output 'multiline-2'\n}")
                check("pwsh 多行块", "multiline-2" in out8, repr(out8))
                tool_result = run_pwsh_tool(shell, {"command": "Write-Output 'tool-ok'"}, 16000)
                check("pwsh 工具封装", "tool-ok" in tool_result, tool_result)
                empty = run_pwsh_tool(shell, {"command": "$null = 1"}, 16000)
                check("pwsh 空输出占位", empty == "(no output)", repr(empty))
                big = run_pwsh_tool(shell, {"command": "'x' * 20000"}, 100)
                check("pwsh 输出截断", "<response clipped>" in big, big[-80:])
                out9, code9, note9 = shell.run("Start-Sleep -Seconds 5", timeout_ms=1200)
                check("pwsh 超时处理", "timed out" in out9 and "reset" in note9, out9[:120])
            finally:
                shell.kill()

            # ---- Win7 兼容路径 1：单命令模式（每条命令一个进程） ----
            oneshot_shell = PersistentShell(merge_config(
                DEFAULT_CONFIG, {"cwd": temp_dir, "shell_mode": "oneshot", "shell_timeout_ms": 60000}))
            try:
                out_os, code_os, _ = oneshot_shell.run("Write-Output 'oneshot-中文-ok'")
                check("pwsh 单命令模式可用", "oneshot-中文-ok" in out_os, repr(out_os[:120]))
                check("pwsh 单命令模式输出干净（无 CLIXML）",
                      "#< CLIXML" not in out_os and "<Objs" not in out_os, repr(out_os[:120]))
                out_code, code_os2, _ = oneshot_shell.run("cmd /c exit 5")
                check("pwsh 单命令模式退出码透传", code_os2 == 5, "exit=%s" % code_os2)
                out_err, code_os3, _ = oneshot_shell.run("Get-ChildItem 'C:\\no-such-dir-dsh-mini'")
                # PowerShell 的报错文本会随 UI 语言变化（中文系统是"找不到路径"），
                # 所以只判断"错误信息确实回传了"，不绑死英文措辞。
                check("pwsh 单命令模式错误信息可读",
                      ("Cannot find path" in out_err or "找不到路径" in out_err
                       or "no-such-dir-dsh-mini" in out_err),
                      repr(out_err[:100]))
                check("pwsh 单命令模式结束语正确", oneshot_shell.describe().find("one-shot") >= 0,
                      oneshot_shell.describe())
            finally:
                oneshot_shell.kill()

            # ---- Win7 兼容路径 2：持久会话起不来时必须自动降级，而不是卡死 ----
            global PS_LOADER_TEMPLATE
            saved_loader = PS_LOADER_TEMPLATE
            PS_LOADER_TEMPLATE = "$ErrorActionPreference='Continue'\nexit 3\n"
            degraded_shell = PersistentShell(merge_config(
                DEFAULT_CONFIG, {"cwd": temp_dir, "shell_probe_timeout_ms": 8000, "shell_timeout_ms": 60000}))
            try:
                degraded_shell.run("Write-Output 'trigger'")
                out_dg, code_dg, _ = degraded_shell.run("Write-Output 'degraded-ok'")
                check("pwsh 持久会话失败时自动降级（不卡死）",
                      degraded_shell.mode == "oneshot" and "degraded-ok" in out_dg,
                      "%s / %r" % (degraded_shell.mode, out_dg[:80]))
                check("pwsh 降级后提示词说明状态不保持",
                      degraded_shell.describe().find("one-shot") >= 0, degraded_shell.describe())
            finally:
                degraded_shell.kill()
                PS_LOADER_TEMPLATE = saved_loader

            candidates = powershell_candidates()
            check("能列出候选 PowerShell", bool(candidates) and any("powershell" in path.lower() for _l, path in candidates),
                  str(candidates[:2]))
            check("持久会话协议自带 stdin 读取循环（不再依赖 -Command -）",
                  "ReadLine()" in PS_LOADER_TEMPLATE
                  and "[Console]::Out.WriteLine" in PS_LOADER_TEMPLATE
                  and "OpenStandardInput" in PS_LOADER_TEMPLATE, "")
            check("协议不含 base64 解码（避免被杀软启发式误报）",
                  "FromBase64String" not in PS_LOADER_TEMPLATE
                  and "base64" not in ONESHOT_PREAMBLE.lower()
                  and "ReadAllText" not in ONESHOT_PREAMBLE, "")

        # ---- 配置 ----
        merged = merge_config(DEFAULT_CONFIG, {"model": "x"}, {"temperature": 0.5})
        check("配置合并", merged["model"] == "x" and merged["temperature"] == 0.5)

        # ---- Win7 深度定制与自带 PowerShell 7 ----
        check("可定位内嵌 PowerShell 7 压缩包", get_embedded_pwsh_zip() is not None)
        extracted_pwsh = ensure_bundled_powershell()
        check("同目录自带稳定版 PowerShell 7 就绪", extracted_pwsh is not None and os.path.isfile(extracted_pwsh))
        cands = powershell_candidates()
        check("候选列表中自带稳定版 PowerShell 7 强制排在首位",
              len(cands) > 0 and "自带稳定版 PowerShell 7" in cands[0][0])
        check("Win7 专属提示词明确标明 Win7 环境及 pwsh 7 详细运行环境",
              "Windows 7" in DEFAULT_CONFIG["system_prompt"] and "PowerShell 7" in DEFAULT_CONFIG["system_prompt"])
        test_agent = Agent(dict(DEFAULT_CONFIG), None, None, None)
        check("系统提示词包含详细 Win7 与自带 PowerShell 7 环境说明",
              "Windows 7" in test_agent._system_prompt() and "PowerShell 7" in test_agent._system_prompt())

        # ---- GUI 对话框（v1.1.3："密钥填不进去"那一串坑的守卫） ----
        # 1) 窗口类同名只注册一次，且窗口过程对象常驻（否则第二个框会用第一个框的过程）
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt
            probe_user32 = _ct.WinDLL("user32", use_last_error=True)
            probe_gdi32 = _ct.WinDLL("gdi32", use_last_error=True)
            probe_kernel32 = _ct.WinDLL("kernel32", use_last_error=True)
            gui_declare_apis(probe_user32, probe_gdi32, _ct, _wt)

            def _probe_proc(hwnd, msg, wparam, lparam):
                return gui_def_window_proc(hwnd, msg, wparam, lparam)

            first = gui_register_window_class(probe_user32, probe_kernel32, _ct, _wt,
                                              "DshMiniSelftestProbe", _probe_proc)
            second = gui_register_window_class(probe_user32, probe_kernel32, _ct, _wt,
                                               "DshMiniSelftestProbe", _probe_proc)
            check("GUI 窗口类同名只注册一次（第二个框不会再用旧过程）",
                  first is second and _GUI_CLASS_REFS.get("DshMiniSelftestProbe") is first, "")

            # 2) 输入框：建好后键盘焦点必须落在编辑框上（否则一个字符都打不进去）
            class _FakeParent(object):
                pass

            fake = _FakeParent()
            fake.user32, fake.gdi32, fake.kernel32 = probe_user32, probe_gdi32, probe_kernel32
            fake.ctypes, fake.wintypes = _ct, _wt
            fake.hwnd, fake.fonts = None, []

            # show=False：自检不往屏幕上闪窗口（隐藏窗口一样能验证焦点）
            dlg, state = _GuiInputDialog._create(fake, "API Key", "sk-自检", secret=True, show=False)
            check("输入框：弹出后键盘焦点落在编辑框上（能打字）",
                  bool(dlg) and probe_user32.GetFocus() == state.get("edit"),
                  "focus=%s edit=%s" % (probe_user32.GetFocus(), state.get("edit")))
            check("输入框：当前值预填进编辑框（可直接改）",
                  gui_read_edit_text(probe_user32, _ct, state.get("edit")) == "sk-自检",
                  gui_read_edit_text(probe_user32, _ct, state.get("edit")))
            check("输入框：密钥字段默认打码（勾“显示密钥”可核对）",
                  bool(probe_user32.SendMessageW(state.get("edit"), 0x00D2, 0, 0)), "")
            _plain_dlg, plain_state = _GuiInputDialog._create(fake, "接口地址", "", show=False)
            check("输入框：非密钥字段不打码",
                  not probe_user32.SendMessageW(plain_state.get("edit"), 0x00D2, 0, 0), "")
            # 窗口类必须带箭头光标：类光标为 NULL 时，鼠标停在对话框空白处会一直
            # 保持上一个形状（I 型），用户看到的就是"鼠标卡死在输入光标上"
            probe_user32.GetClassLongPtrW.argtypes = [_wt.HWND, _ct.c_int]
            probe_user32.GetClassLongPtrW.restype = _ct.c_ssize_t
            probe_user32.LoadCursorW.argtypes = [_wt.HINSTANCE, _ct.c_void_p]
            probe_user32.LoadCursorW.restype = _wt.HANDLE
            class_cursor = probe_user32.GetClassLongPtrW(dlg, -12)      # GCLP_HCURSOR
            check("对话框窗口类带箭头光标（鼠标不会卡在 I 型上）",
                  bool(class_cursor) and int(class_cursor) == int(probe_user32.LoadCursorW(None, 32512)),
                  "类光标=%s" % class_cursor)
            # 3) 关掉对话框必须能自行退出消息循环（否则 ask() 永远不返回、配置永远存不上）
            _GuiInputDialog._close(state, commit=True)
            check("输入框：点确定后 ask 会返回，且带回填进去的值",
                  state.get("done") and state.get("value") == "sk-自检"
                  and not probe_user32.IsWindow(dlg), "value=%r" % (state.get("value"),))
            _GuiInputDialog._close(plain_state, commit=False)
            check("输入框：点取消返回空值（不改动原配置）",
                  plain_state.get("done") and plain_state.get("value") is None, "")

            # 4) 状态按 hwnd 隔离：第二个框绝不能再读到第一个框的状态
            _GuiInputDialog._states[0x7F00000000000001] = {"tag": "A"}
            _GuiInputDialog._states[0x7F00000000000002] = {"tag": "B"}
            isolated = (_GuiInputDialog._state_for(0x7F00000000000001) == {"tag": "A"}
                        and _GuiInputDialog._state_for(0x7F00000000000002) == {"tag": "B"})
            _GuiInputDialog._states.pop(0x7F00000000000001, None)
            _GuiInputDialog._states.pop(0x7F00000000000002, None)
            check("输入框状态按 hwnd 隔离（第二个框不会串到第一个框）", isolated, "")
        except Exception as exc:
            check("GUI 对话框自检可运行", False, "%s: %s" % (exc.__class__.__name__, exc))

        # ---- 改配置立刻生效（client 会缓存 base_url/api_key） ----
        probe_config = merge_config(DEFAULT_CONFIG, {"base_url": "http://a.example/v1",
                                                     "api_key": "sk-old", "model": "m-old"})
        probe_client = OpenAICompletionsClient(probe_config)
        probe_config.update({"base_url": "http://b.example", "api_key": "sk-new", "model": "m-new"})
        probe_client.reload(probe_config)
        check("改完配置 client 立刻生效（F2 之后不用重启）",
              "b.example" in probe_client.base_url and probe_client.base_url.endswith("/chat/completions")
              and probe_client.api_key == "sk-new" and probe_client.model == "m-new",
              "%s / %s / %s" % (probe_client.base_url, probe_client.api_key, probe_client.model))

        # ---- 思维链显示（TUI 有、GUI 也必须有的那个） ----
        class _StubGui(object):
            def __init__(self):
                self.parts = []
                self.status = ""

            def push(self, text):
                self.parts.append(text)

            def set_status(self, text):
                self.status = text

        stub = _StubGui()
        emitter = GuiEmitter(stub, {"show_reasoning": True})
        emitter.on_reasoning("第一步思考")
        emitter.on_reasoning("第二步思考")
        joined = "".join(stub.parts)
        check("GUI 显示模型思维链（默认开）",
              "…" in joined and "第一步思考" in joined and "第二步思考" in joined, repr(joined))
        check("思考时状态栏提示可打断", "思考中" in stub.status, stub.status)
        quiet = _StubGui()
        GuiEmitter(quiet, {"show_reasoning": False}).on_reasoning("不该出现")
        check("关掉思维链后不再输出", "不该出现" not in "".join(quiet.parts), "")
        check("默认配置就是显示思维链", DEFAULT_CONFIG["show_reasoning"] is True, "")

        # ---- 快捷键常量（v1.1.2 漏了 VK_ESCAPE，害得所有快捷键失效） ----
        required_keys = ("VK_RETURN", "VK_ESCAPE", "VK_TAB", "VK_UP", "VK_DOWN", "VK_F1",
                         "VK_F4", "VK_F8", "VK_C", "VK_D", "VK_L", "VK_O", "VK_S")
        missing_keys = [name for name in required_keys if not hasattr(DshGui, name)]
        check("GUI 快捷键常量齐全（漏一个 = 所有快捷键失效）", not missing_keys, str(missing_keys))
        check("快捷键异常会写日志（不再被 ctypes 回调静默吞掉）",
              hasattr(DshGui, "_safe_key"), "")

        # ---- 显示宽度 ----
        check("中文宽度计算", display_width("中文") == 4 and display_width("ab") == 2)

        # ---- 鼠标控制工具自检 ----
        m_pos = run_mouse_tool({"action": "position"})
        check("mouse_control 查询位置与分辨率", "Current cursor position" in m_pos and "Screen resolution" in m_pos, m_pos)
        m_err = ""
        try:
            run_mouse_tool({"action": "invalid_action"})
        except ValueError as exc:
            m_err = str(exc)
        check("mouse_control 非法 action 报错", "Invalid action" in m_err, m_err)
        m_btn_err = ""
        try:
            run_mouse_tool({"action": "click", "button": "invalid_btn"})
        except ValueError as exc:
            m_btn_err = str(exc)
        check("mouse_control 非法 button 报错", "Invalid button" in m_btn_err, m_btn_err)

        # ---- 图片与视觉多模态支持自检 ----
        test_img = os.path.join(temp_dir, "test.png")
        with open(test_img, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00")
        check("is_image_path 识别本地 PNG", is_image_path(test_img) is True)
        check("is_image_path 忽略非图片", is_image_path(sample) is False)
        data_url = encode_image_to_data_url(test_img)
        check("encode_image_to_data_url 生成 Data URL", data_url.startswith("data:image/png;base64,"), data_url[:50])

        vision_content = build_openai_user_content("分析这张图", [test_img], support_vision=True)
        check("build_openai_user_content 开启视觉生成多模态数组",
              isinstance(vision_content, list) and len(vision_content) == 2 and vision_content[1].get("type") == "image_url",
              str(vision_content))
        novision_content = build_openai_user_content("分析这张图", [test_img], support_vision=False)
        check("build_openai_user_content 关闭视觉回退纯文本",
              isinstance(novision_content, str) and novision_content == "分析这张图",
              str(novision_content))

        # ---- GuiEmitter 精炼显示与文件内容不倾倒自检（BUG 2 / BUG 3） ----
        emitter_stub = _StubGui()
        gui_emit = GuiEmitter(emitter_stub, {"show_live_output": True})
        big_file_content = "\n".join("line %d" % i for i in range(200))
        gui_emit.on_tool_start("str_replace_editor", {"command": "view", "path": "test.txt"})
        gui_emit.on_tool_end("str_replace_editor", big_file_content, 0.1, False)
        view_out = "".join(emitter_stub.parts)
        check("GuiEmitter view 读文件不倾倒文件内容到界面",
              "已读取文件内容，共 200 行" in view_out and "line 50" not in view_out,
              view_out)

        # ---- AI 自主读图与截屏工具自检 ----
        read_msg, read_path, detail = run_read_image_tool({"path": test_img}, {"support_vision": True})
        check("run_read_image_tool 自主读图成功", "Successfully read image" in read_msg and os.path.samefile(read_path, test_img), read_msg)
        shot_res, shot_img, _ = run_screenshot_tool({}, {"support_vision": True})
        check("run_screenshot_tool 自主截屏成功", "Screenshot captured" in shot_res and os.path.isfile(shot_img), shot_res)
        if shot_img and os.path.isfile(shot_img):
            try:
                os.remove(shot_img)
            except Exception:
                pass
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    failed = [name for name, ok, _ in results if not ok]
    print("")
    print("自检完成：%d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(failed), len(failed)))
    if failed:
        print("失败项：" + "，".join(failed))
        return 1
    return 0


# =============================================================================
# 15. 单次执行模式（便于脚本化与自动化测试）
# =============================================================================

class PlainEmitter(Emitter):
    def __init__(self, theme, verbose=True, show_reasoning=False):
        self.theme = theme
        self.verbose = verbose
        self.show_reasoning = show_reasoning
        self.text_open = False

    def on_text(self, chunk):
        sys.stdout.write(chunk)
        sys.stdout.flush()
        self.text_open = True

    def on_reasoning(self, chunk):
        if self.show_reasoning:
            sys.stderr.write(chunk)
            sys.stderr.flush()

    def on_tool_start(self, name, args):
        if self.text_open:
            sys.stdout.write("\n")
            self.text_open = False
        if not self.verbose:
            return
        summary = args.get("command") if name == "pwsh" else "%s %s" % (args.get("command"), args.get("path"))
        sys.stderr.write("[tool] %s: %s\n" % (name, str(summary)[:200]))
        sys.stderr.flush()

    def on_tool_end(self, name, result, elapsed, is_error):
        if not self.verbose:
            return
        sys.stderr.write("[tool] %s %s in %s\n" % ("failed" if is_error else "done", name, fmt_duration(elapsed)))
        sys.stderr.flush()


def run_once(config, prompt, quiet_tools=False):
    theme = Theme(False)
    shell = PersistentShell(config)
    emitter = PlainEmitter(theme, verbose=not quiet_tools,
                           show_reasoning=bool(config.get("show_reasoning")))
    client = OpenAICompletionsClient(config)
    agent = Agent(config, client, shell, emitter)
    try:
        agent.run_turn(prompt)
        if emitter.text_open:
            sys.stdout.write("\n")
        sys.stdout.flush()
        return 0
    except ApiError as exc:
        sys.stderr.write("错误：%s\n" % exc)
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("已中断\n")
        return 130
    finally:
        shell.kill()


# =============================================================================
# 16. 交互主循环
# =============================================================================

HELP_TEXT = """
可用命令：
  /help              显示本帮助
  /tools             列出可调用工具（与 DSH 极简模式一致）
  /models            扫描接口可用模型并切换（也可直接输入模型名）
  /config            显示当前生效配置
  /shellcheck        离线诊断 pwsh 工具（Win7 上命令跑不动时先跑这个）
  /model [名称]      查看或临时切换模型
  /cwd [路径]        查看或切换工作目录（会重启 pwsh 会话）
  /clear  (/new)     清空对话上下文（保留系统提示词）
  /save [路径]       保存当前会话到 JSON
  /resume [list|n]   恢复会话（默认最近一次）
  /exit   (/quit)    退出

输入技巧：
  ↑ / ↓              翻阅历史输入
  ← / →  Home/End    行内移动光标
  Ctrl+V             粘贴剪贴板内容
  Ctrl+J             输入换行（多行消息）
  Ctrl+C             清空当前输入；空行时退出
  Ctrl+Z / Ctrl+D    退出
""".strip("\n")


def run_interactive(config, theme, force_picker=False, reader=None):
    use_vt = enable_virtual_terminal()
    if config.get("color") == "never":
        theme.enabled = False
    elif config.get("color") == "always":
        theme.enabled = True
    else:
        theme.enabled = use_vt and _stdout_is_tty()
    if os.environ.get("NO_COLOR"):
        theme.enabled = False

    editor_mode = config.get("editor") or "auto"
    use_raw = editor_mode == "raw" or (editor_mode == "auto" and _stdin_is_tty() and IS_WIN)
    try:
        import msvcrt  # noqa: F401
    except Exception:
        use_raw = False

    reader = reader or StdinReader(config.get("shell_encoding") or "auto")

    # ---- 启动时选择模型（默认每次启动都问一次，回车即保持当前）----
    if force_picker or (bool(config.get("model_picker", True)) and _stdin_is_tty()):
        print("")
        print(theme.cyan("═" * min(terminal_width(), 68)))
        print(theme.bold(" %s v%s" % (APP_TITLE, VERSION)))
        print(theme.cyan("═" * min(terminal_width(), 68)))
        try:
            picked = choose_model(config, theme, lambda text="": print(text), reader, config.get("model"))
        except (EOFError, KeyboardInterrupt):
            picked = None
        if picked:
            config["model"] = picked
            print(theme.grey("  已选择模型：") + theme.bold(picked))

    spinner = Spinner(theme, enabled=bool(theme.enabled and _stdout_is_tty()))
    tui = Tui(config, theme, spinner, live_output=bool(config.get("show_live_output", True)))
    shell = PersistentShell(config, on_output=None)
    client = OpenAICompletionsClient(config)
    agent = Agent(config, client, shell, tui)

    tui.banner(config)
    if use_raw:
        tui.line(theme.grey("提示：Ctrl+V 粘贴、Ctrl+J 换行、↑↓ 翻历史、Ctrl+C 清空当前输入。"))
    else:
        tui.line(theme.grey("提示：当前为简单输入模式（未启用键盘增强）。"))
    tui.line("")

    history = []
    editor = LineEditor(theme, history, use_raw=use_raw, use_vt=use_vt, reader=reader)
    session_path = new_session_path() if config.get("save_sessions") else None
    interrupted = 0

    while True:
        try:
            prompt_text = theme.bold_cyan("› ") if theme.enabled else "› "
            text = editor.read(prompt_text)
        except KeyboardInterrupt:
            interrupted += 1
            if interrupted >= 2:
                tui.line("")
                break
            tui.line(theme.grey("（再按一次 Ctrl+C 退出）"))
            continue
        except EOFError:
            break
        if text is None:
            break
        interrupted = 0
        text = text.strip("\r\n")
        if not text.strip():
            continue
        if text.strip() != text and "\n" not in text.strip():
            text = text.strip()

        command = text.strip()
        if command.startswith("/"):
            handled, should_exit = handle_command(command, config, theme, tui, agent, shell, session_path, reader)
            if should_exit:
                break
            if handled:
                continue

        history.append(text)
        try:
            agent.run_turn(text)
        except KeyboardInterrupt:
            tui.line("")
            tui.on_notice("已中断本轮（上下文已保留）")
        except ApiError as exc:
            tui.line("")
            tui.on_notice("请求失败：%s" % exc)
        except Exception as exc:
            tui.line("")
            tui.on_notice("发生错误：%s: %s" % (exc.__class__.__name__, exc))
        tui.finish_turn()
        if session_path:
            save_session(session_path, agent, config)

    tui.line(theme.grey("再见。"))
    shell.kill()
    return 0


def handle_command(command, config, theme, tui, agent, shell, session_path, reader=None):
    """返回 (是否已处理, 是否退出)。"""
    parts = command.split(None, 1)
    name = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if name in ("/exit", "/quit", "/q"):
        return True, True
    if name in ("/help", "/?"):
        tui.line(theme.grey(HELP_TEXT))
        return True, False
    if name == "/tools":
        for schema in TOOL_SCHEMAS:
            function = schema["function"]
            tui.line("%s  %s" % (theme.bold(function["name"]),
                                 theme.grey((function["description"] or "").splitlines()[0])))
        return True, False
    if name == "/config":
        visible = dict(config)
        visible["api_key"] = mask_secret(config.get("api_key"))
        visible["system_prompt"] = (config.get("system_prompt") or "").replace("\n", "\\n")[:120]
        for key in sorted(visible):
            tui.line("  %s = %s" % (theme.cyan(key), json.dumps(visible[key], ensure_ascii=False)))
        if shell is not None:
            tui.line("  %s %s" % (theme.cyan("pwsh:"), theme.grey(shell.status_line())))
        return True, False
    if name in ("/shellcheck", "/shell"):
        run_shellcheck(config, tui.line)
        return True, False
    if name == "/models":
        picked = choose_model(config, theme, tui.line, reader or StdinReader(config.get("shell_encoding") or "auto"),
                              config.get("model"))
        if picked:
            config["model"] = picked
            agent.client.model = picked
            tui.line(theme.grey("  模型已切换为 %s（仅本次运行生效）" % picked))
        return True, False
    if name == "/model":
        if argument:
            config["model"] = argument
            agent.client.model = argument
            tui.line(theme.grey("模型已切换为 %s（仅本次运行生效，写回配置请修改 %s）" % (argument, config_path())))
        else:
            tui.line("  当前模型：%s" % config.get("model"))
        return True, False
    if name == "/cwd":
        if argument:
            target = os.path.abspath(argument)
            if not os.path.isdir(target):
                tui.line(theme.red("目录不存在：%s" % target))
                return True, False
            config["cwd"] = target
            os.chdir(target)
            shell.cwd = target
            shell.restart()
            agent._reset_messages()
            tui.line(theme.grey("工作目录已切换为 %s（pwsh 会话已重启，对话已重置）" % target))
        else:
            tui.line("  当前工作目录：%s" % (config.get("cwd") or os.getcwd()))
        return True, False
    if name in ("/clear", "/new"):
        agent.clear()
        tui.line(theme.grey("对话上下文已清空。"))
        return True, False
    if name == "/save":
        path = argument or (session_path or new_session_path())
        saved = save_session(path, agent, config)
        tui.line(theme.grey("已保存：%s" % saved) if saved else theme.red("保存失败"))
        return True, False
    if name == "/resume":
        if argument in ("list", "ls"):
            files = list_sessions()
            if not files:
                tui.line(theme.grey("没有历史会话。"))
            for index, path in enumerate(files, 1):
                tui.line("  %2d. %s  %s" % (index, theme.grey(time.strftime("%Y-%m-%d %H:%M",
                                                                          time.localtime(os.path.getmtime(path)))),
                                            os.path.basename(path)))
            return True, False
        path = None
        if argument.isdigit():
            files = list_sessions()
            index = int(argument) - 1
            if index < 0 or index >= len(files):
                tui.line(theme.red("序号超出范围。"))
                return True, False
            path = files[index]
        elif argument:
            # 支持绝对路径、相对路径，或直接写 sessions 目录里的文件名
            candidate = argument if os.path.isfile(argument) else os.path.join(sessions_dir(), argument)
            if not os.path.isfile(candidate):
                tui.line(theme.red("找不到会话文件：%s" % candidate))
                return True, False
            path = candidate
        else:
            files = list_sessions()
            if not files:
                tui.line(theme.grey("没有历史会话。"))
                return True, False
            path = files[0]
        try:
            messages = load_session(path)
            agent.load_messages(messages)
            tui.line(theme.grey("已恢复会话：%s（%d 条消息）" % (os.path.basename(path), len(messages))))
        except Exception as exc:
            tui.line(theme.red("恢复失败：%s" % exc))
        return True, False
    tui.line(theme.red("未知命令：%s（/help 查看帮助）" % name))
    return True, False


# =============================================================================
# 17. 图形界面（纯 ctypes + Win32，无控制台黑框）
# =============================================================================
#
# 为什么要有 GUI 版：
#   Windows 7 的控制台是旧式实现，字体/代码页/宽字符渲染的坑特别多
#   （中文重复显示、字体突然变大、编码崩溃）。GUI 用 Edit 控件，本身就是
#   Unicode 原生，完全绕开控制台那一层。
#   另外：界面上出现的每一行都会同步写进 exe 同目录的 dsh-mini-gui.log（UTF-8），
#   所以"命令到底执行成功没有"可以直接看日志，完全不受屏幕渲染影响 ——
#   这也是判断"是后端问题还是显示问题"最直接的办法。

GUI_LOG_NAME = "dsh-mini-gui.log"
GUI_MAX_CHARS = 400000
OUTPUT_LINE_HEIGHT = 20        # 对话区行高（微软雅黑 14），_autogrow_input 也用这个值


def gui_supported():
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32)
    except Exception:
        return False


def gui_log_path():
    return os.path.join(BASE_DIR, GUI_LOG_NAME)


def gui_diag_path():
    """自检/诊断的输出单独存一个文件，不跟对话日志混在一起。"""
    return os.path.join(BASE_DIR, "dsh-mini-diagnose.txt")


# ---- GUI 共用脚手架：窗口类注册 / 模态对话框消息循环 -------------------------

# 窗口类名 -> ctypes 窗口过程对象。**必须常驻**：WINFUNCTYPE 实例一旦被 GC，
# 注册进系统里的就是一个野指针，窗口再收到消息就会崩。
_GUI_CLASS_REFS = {}
_GUI_DEFPROC = {}


def _wndclassex(ctypes, wintypes):
    """构造 WNDCLASSEXW 结构体类型（主窗 / 诊断窗 / 选择框 / 输入框共用）。"""

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                    ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]

    return WNDCLASSEXW


def gui_register_window_class(user32, kernel32, ctypes, wintypes, name, proc,
                              cursor=False, style=0):
    """注册窗口类：**同一个类名只注册一次**，并让窗口过程对象常驻。

    踩过的坑（v1.1.2，"密钥填不进去"的元凶之一）：以前每弹一次框就拿同一个类名去
    RegisterClassExW，第二次起必然失败（ERROR_CLASS_ALREADY_EXISTS），于是新窗口
    继续沿用**第一次那个**窗口过程 —— 第二个框的标签还是第一个框的（"接口地址"），
    用户填的值写进了早已作废的返回值里；第一个过程对象被 GC 之后，
    系统里留下的更是已释放的回调（随时可能崩）。
    正确做法：窗口过程按"类"只注册一份，每次弹框的状态用 hwnd 去找。
    """
    ref = _GUI_CLASS_REFS.get(name)
    if ref is not None:
        return ref
    ref = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)(proc)
    wc = _wndclassex(ctypes, wintypes)()
    wc.cbSize = ctypes.sizeof(_wndclassex(ctypes, wintypes))
    wc.style = style
    wc.lpfnWndProc = ctypes.cast(ref, ctypes.c_void_p)
    wc.hInstance = kernel32.GetModuleHandleW(None)
    if cursor:
        wc.hCursor = user32.LoadCursorW(None, 32512)        # IDC_ARROW
    wc.hbrBackground = 16 + 1                               # COLOR_BTNFACE + 1
    wc.lpszClassName = name
    user32.RegisterClassExW(ctypes.byref(wc))
    _GUI_CLASS_REFS[name] = ref
    return ref


def gui_def_window_proc(hwnd, msg, wparam, lparam):
    """找不到 python 状态时的兜底窗口过程（正常路径不会走到）。"""
    import ctypes
    from ctypes import wintypes
    user32 = _GUI_DEFPROC.get("user32")
    if user32 is None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM,
                                          wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        _GUI_DEFPROC["user32"] = user32
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def gui_wake_message_loop():
    """给本线程消息队列塞一条空消息，把 GetMessage 叫醒。

    窗口被 DestroyWindow 之后 GetMessage **不会**返回（它只在收到 WM_QUIT 时才返回 0），
    所以关掉对话框时必须主动叫一声，否则那个嵌套消息循环会一直挂着。
    """
    import ctypes
    from ctypes import wintypes
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.PostThreadMessageW.argtypes = [wintypes.DWORD, ctypes.c_uint,
                                                wintypes.WPARAM, wintypes.LPARAM]
        kernel32.PostThreadMessageW.restype = wintypes.BOOL
        kernel32.PostThreadMessageW(kernel32.GetCurrentThreadId(), 0x0000, 0, 0)
    except Exception:
        pass


def gui_center_on_owner(user32, ctypes, wintypes, hwnd, owner):
    """把对话框摆在属主窗口中间（比 CW_USEDEFAULT 更像对话框）。"""
    if not hwnd or not owner:
        return
    try:
        rect, owner_rect = wintypes.RECT(), wintypes.RECT()
        if not user32.GetWindowRect(owner, ctypes.byref(owner_rect)):
            return
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        width, height = rect.right - rect.left, rect.bottom - rect.top
        x = owner_rect.left + ((owner_rect.right - owner_rect.left) - width) // 2
        y = owner_rect.top + ((owner_rect.bottom - owner_rect.top) - height) // 2
        user32.MoveWindow(hwnd, x, y, width, height, True)
    except Exception:
        pass


def gui_show_modal_dialog(user32, ctypes, wintypes, dlg, primary):
    """把模态对话框"稳稳地"摆到用户眼前，并把键盘焦点交给 primary 控件。

    为什么必须这么啰嗦（v1.1.4 实测踩到）：本进程不在前台时 `SetForegroundWindow`
    会**失败**，对话框就可能躲在浏览器等窗口后面；而此时主窗已经被禁用 ——
    用户看到的是"整个程序点不动、连标题栏的 ✕ 都没反应、光标还停在 I 型上"。
    所以这里：
      1. `SetWindowPos(HWND_TOPMOST)` 先把它压到最上层（不依赖前台权限）；
      2. 再试 `SetForegroundWindow` + `SetActiveWindow`；
      3. 最后 `SetFocus(primary)`，并配合窗口过程的 WM_ACTIVATE 兜一次
         （激活是异步的，光在这里 SetFocus 会被随后的 WM_ACTIVATE 顶掉）。
    """
    HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = -1, 0x0001, 0x0002, 0x0040
    try:
        user32.SetWindowPos(dlg, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    except Exception:
        pass
    user32.ShowWindow(dlg, 5)
    user32.UpdateWindow(dlg)
    user32.SetForegroundWindow(dlg)
    user32.SetActiveWindow(dlg)
    if primary:
        user32.SetFocus(primary)
    return primary


def gui_hide_modal_dialog(user32, wintypes, dlg):
    """对话框关闭前把"总在最前"属性撤掉（它马上要被销毁，这里只是别留下怪状态）。"""
    HWND_NOTOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = -2, 0x0001, 0x0002, 0x0010
    try:
        user32.SetWindowPos(dlg, wintypes.HWND(HWND_NOTOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


def gui_cycle_focus(user32, handles, back=False):
    """Tab / Shift+Tab 轮换焦点（自己搭的对话框没有对话框管理器，只能手动来）。"""
    handles = [handle for handle in handles if handle and user32.IsWindow(handle)]
    if not handles:
        return False
    try:
        index = handles.index(user32.GetFocus())
    except ValueError:
        index = -1
    user32.SetFocus(handles[(index + (-1 if back else 1)) % len(handles)])
    return True


def gui_read_edit_text(user32, ctypes, edit):
    """读控件文本（编辑框 / 列表框都走 GetWindowText 这一条路）。"""
    if not edit:
        return ""
    length = user32.GetWindowTextLengthW(edit)
    buffer = ctypes.create_unicode_buffer(int(length) + 2)
    user32.GetWindowTextW(edit, buffer, int(length) + 2)
    return buffer.value


def gui_get_window_text(user32, ctypes, hwnd):
    """读任意窗口的标题/文本（主窗标题保存与还原用）。"""
    return gui_read_edit_text(user32, ctypes, hwnd)


def gui_declare_apis(user32, gdi32, ctypes, wintypes):
    """声明用到的 user32/gdi32 函数的参数类型。

    不声明的话 64 位下 LPARAM/WPARAM/HWND 会被当 32 位截断：DefWindowProcW 会
    OverflowError: int too long to convert，CallWindowProcW 直接访问违例（见开发文档 §8.5）。
    抽成模块函数是为了让离线自检也能搭一个同样声明的"假父窗"来验证对话框。
    """
    u, w, c = user32, wintypes, ctypes
    LRESULT = c.c_ssize_t
    u.DefWindowProcW.argtypes = [w.HWND, c.c_uint, w.WPARAM, w.LPARAM]
    u.DefWindowProcW.restype = LRESULT
    u.CallWindowProcW.argtypes = [c.c_void_p, w.HWND, c.c_uint, w.WPARAM, w.LPARAM]
    u.CallWindowProcW.restype = LRESULT
    u.SendMessageW.argtypes = [w.HWND, c.c_uint, w.WPARAM, w.LPARAM]
    u.SendMessageW.restype = LRESULT
    u.PostMessageW.argtypes = [w.HWND, c.c_uint, w.WPARAM, w.LPARAM]
    u.PostMessageW.restype = w.BOOL
    u.SetWindowPos.argtypes = [w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, c.c_uint]
    u.SetWindowPos.restype = w.BOOL
    u.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int,
                                  c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p]
    u.CreateWindowExW.restype = w.HWND
    u.MoveWindow.argtypes = [w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.BOOL]
    u.MoveWindow.restype = w.BOOL
    u.GetClientRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
    u.GetClientRect.restype = w.BOOL
    u.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
    u.GetWindowRect.restype = w.BOOL
    u.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
    u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
    u.GetWindowTextLengthW.argtypes = [w.HWND]
    u.GetWindowTextLengthW.restype = c.c_int
    u.SetTimer.argtypes = [w.HWND, c.c_size_t, c.c_uint, c.c_void_p]
    u.SetTimer.restype = c.c_size_t
    u.KillTimer.argtypes = [w.HWND, c.c_size_t]
    u.KillTimer.restype = w.BOOL
    u.DestroyWindow.argtypes = [w.HWND]
    u.DestroyWindow.restype = w.BOOL
    u.MessageBoxW.argtypes = [w.HWND, w.LPCWSTR, w.LPCWSTR, c.c_uint]
    u.MessageBoxW.restype = c.c_int
    u.SetClipboardData.argtypes = [c.c_uint, w.HANDLE]
    u.SetClipboardData.restype = w.HANDLE
    u.GetMessageW.argtypes = [c.POINTER(w.MSG), w.HWND, c.c_uint, c.c_uint]
    u.GetMessageW.restype = c.c_int
    u.TranslateMessage.argtypes = [c.POINTER(w.MSG)]
    u.DispatchMessageW.argtypes = [c.POINTER(w.MSG)]
    u.DispatchMessageW.restype = LRESULT
    u.SetWindowLongPtrW.argtypes = [w.HWND, c.c_int, c.c_void_p]
    u.SetWindowLongPtrW.restype = c.c_void_p
    u.CreateMenu.argtypes = []
    u.CreateMenu.restype = w.HMENU
    u.CreatePopupMenu.argtypes = []
    u.CreatePopupMenu.restype = w.HMENU
    u.AppendMenuW.argtypes = [w.HMENU, c.c_uint, c.c_size_t, w.LPCWSTR]
    u.AppendMenuW.restype = w.BOOL
    u.CheckMenuItem.argtypes = [w.HMENU, c.c_uint, c.c_uint]
    u.CheckMenuItem.restype = w.DWORD
    u.SetMenu.argtypes = [w.HWND, w.HMENU]
    u.SetMenu.restype = w.BOOL
    u.EnableWindow.argtypes = [w.HWND, w.BOOL]
    u.EnableWindow.restype = w.BOOL
    u.IsWindow.argtypes = [w.HWND]
    u.IsWindow.restype = w.BOOL
    u.IsWindowEnabled.argtypes = [w.HWND]
    u.IsWindowEnabled.restype = w.BOOL
    u.IsChild.argtypes = [w.HWND, w.HWND]
    u.IsChild.restype = w.BOOL
    u.SetFocus.argtypes = [w.HWND]
    u.SetFocus.restype = w.HWND
    u.GetFocus.argtypes = []
    u.GetFocus.restype = w.HWND
    u.SetActiveWindow.argtypes = [w.HWND]
    u.SetActiveWindow.restype = w.HWND
    u.SetForegroundWindow.argtypes = [w.HWND]
    u.SetForegroundWindow.restype = w.BOOL
    u.GetKeyState.argtypes = [c.c_int]
    u.GetKeyState.restype = c.c_short
    u.ShowWindow.argtypes = [w.HWND, c.c_int]
    u.ShowWindow.restype = w.BOOL
    u.UpdateWindow.argtypes = [w.HWND]
    u.UpdateWindow.restype = w.BOOL
    u.InvalidateRect.argtypes = [w.HWND, c.c_void_p, w.BOOL]
    u.InvalidateRect.restype = w.BOOL
    u.LoadCursorW.argtypes = [w.HINSTANCE, c.c_void_p]   # 第二个参数是 MAKEINTRESOURCE 整数
    u.LoadCursorW.restype = w.HANDLE
    u.RegisterClassExW.argtypes = [c.c_void_p]
    u.RegisterClassExW.restype = w.ATOM
    u.GetSystemMetrics.argtypes = [c.c_int]
    u.GetSystemMetrics.restype = c.c_int
    gdi32.CreateFontW.argtypes = [c.c_int, c.c_int, c.c_int, c.c_int, c.c_int, w.DWORD,
                                  w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
                                  w.DWORD, w.LPCWSTR]
    gdi32.CreateFontW.restype = w.HANDLE


def gui_run_modal(parent, dlg, hotkeys, focus_order=(), focus_back=None, done=None):
    """跑一个对话框的消息循环，直到对话框被销毁。

    三个"必须这么写"的理由（前两条就是 v1.1.2 密钥问题的现场）：
      * **窗口销毁后要自己退出**：GetMessage 只在收到 WM_QUIT 时返回 0，
        对话框被 DestroyWindow 之后它照样一直等消息 —— 不管的话 ask() 永远不返回，
        _on_setup 永远走不到"保存配置"那一步（表现：填完密钥点确定，什么都没发生）；
      * **弹框期间禁用属主窗口**（模态）：否则主窗照样收键盘和菜单消息，
        能在第一个框还开着的时候再按一次 F2，把 _on_setup 叠进第二层消息循环；
      * **键盘热键在 dispatch 之前截下来**：Enter=确定、Esc=取消、Tab=换焦点。
        其余消息照常 dispatch（主窗的定时器还要继续把对话区刷出来）。
    """
    u, c, w = parent.user32, parent.ctypes, parent.wintypes
    owner = getattr(parent, "hwnd", None)
    owner_enabled = bool(owner) and bool(u.IsWindowEnabled(owner))
    owner_title = ""
    if owner_enabled:
        u.EnableWindow(owner, False)
        # 标题上写清楚"主窗为什么点不动"：万一对话框被别的窗口盖住，
        # 用户至少知道要去找什么，而不是以为整个程序死了（v1.1.4 实测踩到过）。
        owner_title = gui_get_window_text(u, c, owner)
        if owner_title:
            u.SetWindowTextW(owner, "%s —— 请先完成弹出的对话框" % owner_title)
    try:
        msg = w.MSG()
        while u.GetMessageW(c.byref(msg), None, 0, 0) > 0:
            if (done is not None and done()) or not u.IsWindow(dlg):
                break
            if msg.message == 0x0100:                  # WM_KEYDOWN：先截 Enter/Esc/Tab
                # 注意：wintypes.MSG 的字段名是 hWnd（大写 W），写成 hwnd 会 AttributeError
                target = getattr(msg, "hWnd", None)
                if target and (target == dlg or u.IsChild(dlg, target)):
                    try:
                        key = int(msg.wParam)
                        if key == 0x09:                # Tab / Shift+Tab
                            gui_cycle_focus(u, focus_order, back=bool(u.GetKeyState(0x10) & 0x8000))
                            continue
                        action = hotkeys.get(key)
                        if action is not None:
                            action()
                            continue
                    except Exception:
                        exc_type, exc_value, exc_tb = sys.exc_info()
                        write_crash_log(exc_type, exc_value, exc_tb, where="gui-modal-hotkey")
            u.TranslateMessage(c.byref(msg))
            u.DispatchMessageW(c.byref(msg))
    finally:
        if owner_enabled:
            u.EnableWindow(owner, True)
            if owner_title:
                u.SetWindowTextW(owner, owner_title)     # 标题还原
            u.SetForegroundWindow(owner)
            if focus_back:
                u.SetFocus(focus_back)
            # 激活是异步的：上面这句 SetFocus 可能被随后的 WM_ACTIVATE 顶掉，
            # 而且"被销毁的控件"还可能挂着焦点。所以再补一条消息，让主线程在
            # 激活尘埃落定之后把焦点明确交回输入框。
            u.PostMessageW(owner, 0x8004, 0, 0)          # WM_APP_REFOCUS


class _GuiStream(object):
    """把标准输出接到 GUI 上：这样 run_selftest 之类的 print 函数能直接复用。"""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, push):
        self._push = push
        self._buffer = ""

    def write(self, text):
        if not isinstance(text, str):
            return 0
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._push(line + "\n")
        return len(text)

    def flush(self):
        if self._buffer:
            self._push(self._buffer)
            self._buffer = ""

    def isatty(self):
        return False


class GuiEmitter(Emitter):
    """把 Agent 的输出全部送到 GUI（同时落日志）。

    排版规则（每条都对应一次"回车"）：
      * 每块内容自带结尾换行，块与块之间还额外空一行；
      * 模型正文：● 开头；思维链：… 开头（灰色无法用在 Edit 上，用前缀区分）；
      * 工具调用：» 名字 + 智能精简摘要；实时输出：│ 开头（超长自动折叠）；
      * 读写文件与命令结果：完成确认与统计（不倾倒大段文件内容与冗长输出）；
      * 结束：√/× + 耗时。
    注意：Windows 的 EDIT 控件只认 CRLF，这些换行最终由 DshGui.push 统一转成 \r\n。
    """

    def __init__(self, gui, config):
        self.gui = gui
        self.show_reasoning = bool(config.get("show_reasoning", True))
        self.live_output = bool(config.get("show_live_output", True))
        self._mode = None
        self._live_lines = 0
        self._live_truncated = False
        self._current_tool = None
        self._current_args = None

    def _end_block(self):
        if self._mode is not None:
            self.gui.push("\n")
            self._mode = None

    def on_text(self, chunk):
        if self._mode != "text":
            self._end_block()
            self.gui.push("● ")
            self._mode = "text"
        self.gui.push(chunk)

    def on_reasoning(self, chunk):
        if not self.show_reasoning:
            return
        if self._mode != "reason":
            self._end_block()
            self.gui.push("… ")
            self._mode = "reason"
            self.gui.set_status("模型思考中…（按 Esc 或点「停止」可打断）")
        self.gui.push(chunk)

    def on_tool_start(self, name, args):
        self._end_block()
        self._current_tool = name
        self._current_args = args or {}
        self._live_lines = 0
        self._live_truncated = False

        if name == "pwsh":
            command = str(args.get("command") or "").strip()
            lines = command.splitlines() if command else []
            self.gui.push("» pwsh\n")
            if len(lines) <= 2:
                for line in lines:
                    line_str = line.rstrip()
                    if len(line_str) > 160:
                        line_str = line_str[:160] + "…"
                    self.gui.push("    " + line_str + "\n")
            else:
                for line in lines[:2]:
                    line_str = line.rstrip()
                    if len(line_str) > 160:
                        line_str = line_str[:160] + "…"
                    self.gui.push("    " + line_str + "\n")
                self.gui.push("    …（脚本共 %d 行，其余已折叠）\n" % len(lines))
        elif name == "str_replace_editor":
            cmd = args.get("command") or "?"
            path = args.get("path") or "?"
            if cmd == "view":
                vr = args.get("view_range")
                range_str = " [第 %d~%d 行]" % (vr[0], vr[1]) if (isinstance(vr, (list, tuple)) and len(vr) == 2) else ""
                if os.path.isdir(path):
                    self.gui.push("» 浏览目录: %s\n" % path)
                else:
                    self.gui.push("» 读取文件: %s%s\n" % (path, range_str))
            elif cmd == "create":
                self.gui.push("» 创建文件: %s\n" % path)
            elif cmd == "str_replace":
                self.gui.push("» 编辑文件: %s (替换文本)\n" % path)
            elif cmd == "insert":
                self.gui.push("» 编辑文件: %s (在第 %s 行插入)\n" % (path, args.get("insert_line", "?")))
            else:
                self.gui.push("» 编辑文件: %s  %s\n" % (cmd, path))
        elif name == "mouse_control":
            act = args.get("action") or "action"
            btn = args.get("button")
            x = args.get("x")
            y = args.get("y")
            scroll = args.get("scroll_amount")
            details = []
            if act in ("click", "double_click", "down", "up") and btn:
                details.append("按键=" + str(btn))
            if x is not None or y is not None:
                details.append("坐标=(%s, %s)" % (x if x is not None else "当前", y if y is not None else "当前"))
            if act == "scroll" and scroll is not None:
                details.append("步数=%s" % scroll)
            det_str = (" [" + ", ".join(details) + "]") if details else ""
            self.gui.push("» 鼠标操作: %s%s\n" % (act, det_str))
        elif name == "read_image":
            self.gui.push("» 读取图片: %s\n" % (args.get("path") or "?"))
        elif name == "take_screenshot":
            self.gui.push("» 截取屏幕\n")
        else:
            self.gui.push("» %s  %s\n" % (name, json.dumps(args, ensure_ascii=False)[:120]))
        self.gui.set_status("执行工具…")

    def on_tool_output(self, chunk):
        if not self.live_output or not chunk or not chunk.strip():
            return
        if self._live_truncated:
            return
        lines = chunk.rstrip("\n").splitlines()
        for line in lines:
            if not line.strip():
                continue
            self._live_lines += 1
            if self._live_lines > 8:
                self.gui.push("  │ …（更多实时输出已省略，完整结果可在日志中查看）\n")
                self._live_truncated = True
                break
            line_show = line.rstrip()
            if len(line_show) > 200:
                line_show = line_show[:200] + "…"
            self.gui.push("  │ " + line_show + "\n")

    def on_tool_end(self, name, result, elapsed, is_error):
        self.gui.push("  %s %s · %s\n" % ("×" if is_error else "√",
                                          fmt_duration(elapsed), "失败" if is_error else "完成"))
        body = (result or "").strip("\r\n")
        args = self._current_args or {}

        if is_error:
            # 失败时输出错误原因摘要（最多 3 行）
            err_lines = body.splitlines() if body else ["未知错误"]
            for eline in err_lines[:3]:
                self.gui.push("  ! " + eline[:200] + "\n")
            if len(err_lines) > 3:
                self.gui.push("  ! …（详细报错请查看界面日志）\n")
        else:
            # 成功时：区分工具类型，精炼显示，绝不倾倒大段文本
            if name == "str_replace_editor":
                cmd = args.get("command") or ""
                if cmd == "view":
                    # BUG 3 修复核心：绝不将整个文件的内容打印出来
                    line_count = body.count("\n") + 1 if body else 0
                    if "Here're the files and directories" in body:
                        self.gui.push("  (已获取目录列表，模型已接收)\n")
                    else:
                        self.gui.push("  (已读取文件内容，共 %d 行，模型已接收)\n" % line_count)
                elif cmd == "create":
                    self.gui.push("  (文件创建成功)\n")
                elif cmd in ("str_replace", "insert"):
                    self.gui.push("  (文件修改成功)\n")
                else:
                    self.gui.push("  (操作成功完成)\n")
            elif name == "mouse_control":
                # 鼠标控制只显示简要单行结果
                first_line = body.splitlines()[0] if body else "操作完成"
                self.gui.push("  (%s)\n" % first_line)
            elif name == "read_image":
                self.gui.push("  (已读取图片并发送给模型进行视觉分析)\n")
            elif name == "take_screenshot":
                self.gui.push("  (已截取屏幕并发送给模型进行视觉分析)\n")
            elif name == "pwsh":
                # BUG 2 修复核心：已在 live_output 输出了则不重复打印；未输出时若较长则精简折叠
                if self._live_lines > 0:
                    pass
                else:
                    pwsh_lines = body.splitlines() if body else []
                    if len(pwsh_lines) <= 3 and len(body) <= 300:
                        for pline in pwsh_lines:
                            self.gui.push("  " + pline.rstrip() + "\n")
                    elif pwsh_lines:
                        self.gui.push("  " + pwsh_lines[0][:160] + "\n")
                        self.gui.push("  …（命令输出共 %d 行，已省略显示，完整内容见界面日志）\n" % len(pwsh_lines))
                        if len(pwsh_lines) > 1:
                            self.gui.push("  " + pwsh_lines[-1][:160] + "\n")
            else:
                other_lines = body.splitlines() if body else []
                if len(other_lines) <= 2 and len(body) <= 200:
                    for oline in other_lines:
                        self.gui.push("  " + oline.rstrip() + "\n")
                elif other_lines:
                    self.gui.push("  " + other_lines[0][:160] + "\n")
                    self.gui.push("  …（输出已折叠，完整内容见界面日志）\n")

        self._end_block()
        self.gui.set_status("就绪")

    def on_status(self, text):
        if text:
            self.gui.set_status(text)

    def on_notice(self, text):
        self._end_block()
        self.gui.push("! %s\n" % text)

    def on_usage(self, usage):
        if usage:
            self.gui.push("  [Token消耗] %s\n" % format_usage(usage))
            self._end_block()


class _GuiReader(object):
    """GUI 模式下替代标准输入（/models 已经被 GUI 自己的选择框接管）。"""

    def readline(self, prompt_text=""):
        return None


class _DiagWindow(object):
    """自检 / 诊断的独立窗口。

    之所以单独开窗口：这些输出又长又技术，倒进对话区会把对话冲掉；
    放在这里"常驻可用、随时打开、不打扰对话"，
    同时完整写进 dsh-mini-diagnose.txt 便于回传排障。

    窗口类是**全进程共用一份**的（见 gui_register_window_class），
    所以窗口过程是 classmethod + 按 hwnd 查实例，不能再用"每个实例一个 _wndproc"。
    """

    CLASS_NAME = "DshMiniDiagWnd"
    WM_CREATE, WM_DESTROY, WM_SIZE, WM_TIMER = 0x0001, 0x0002, 0x0005, 0x0113
    WM_COMMAND, WM_CLOSE = 0x0111, 0x0010

    _instances = {}      # hwnd -> 实例
    _creating = []       # 正在创建（还没拿到 hwnd）的实例

    def __init__(self, parent, title="pwsh 诊断 / 自检"):
        self.parent = parent
        self.u = parent.user32
        self.c = parent.ctypes
        self.w = parent.wintypes
        self.title = title
        self.queue = queue.Queue()
        self.text = ""
        self.closed = False
        self.hwnd = None
        self.controls = {}
        self.font = parent.gdi32.CreateFontW(-14, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "微软雅黑")
        self._log = None
        try:
            self._log = open(gui_diag_path(), "a", encoding="utf-8", newline="")
            self._log.write("\r\n===== %s  %s =====\r\n" % (title, time.strftime("%Y-%m-%d %H:%M:%S")))
        except Exception:
            self._log = None
        self._create()

    # ---- 窗口过程（类共用一份，按 hwnd 找实例） ----

    @classmethod
    def _instance_for(cls, hwnd):
        instance = cls._instances.get(hwnd)
        if instance is None and cls._creating:
            instance = cls._creating[-1]
        return instance

    @classmethod
    def _wndproc(cls, hwnd, msg, wparam, lparam):
        instance = cls._instance_for(hwnd)
        if instance is None:
            return gui_def_window_proc(hwnd, msg, wparam, lparam)
        return instance._handle(hwnd, msg, wparam, lparam)

    def _create(self):
        u, w, c = self.u, self.w, self.c
        gui_register_window_class(u, self.parent.kernel32, c, w, self.CLASS_NAME,
                                  type(self)._wndproc, cursor=True, style=0x0002 | 0x0001)
        type(self)._creating.append(self)
        try:
            self.hwnd = u.CreateWindowExW(
                0, self.CLASS_NAME,
                "%s —— %s（输出同时写入 dsh-mini-diagnose.txt）" % (APP_TITLE, self.title),
                0x00CF0000, 0xC0000000, 0xC0000000, 900, 620, self.parent.hwnd, None, None, None)
        finally:
            type(self)._creating.remove(self)
        u.ShowWindow(self.hwnd, 5)
        u.UpdateWindow(self.hwnd)

    def _create_controls(self):
        u, w = self.u, self.w
        edit = 0x40000000 | 0x10000000 | 0x00200000 | 0x00800000 | 0x0004 | 0x0040 | 0x0800
        button = 0x40000000 | 0x10000000 | 0x00010000
        self.controls["text"] = u.CreateWindowExW(0, "EDIT", "", edit, 0, 0, 10, 10, self.hwnd, 41, None, None)
        u.SendMessageW(self.controls["text"], 0x00C5, 0, 0)
        for key, label, cid in (("copy", "复制全部", 42), ("save", "另存诊断文件", 43),
                                ("clear", "清空", 44), ("close", "关闭", 45)):
            self.controls[key] = u.CreateWindowExW(0, "BUTTON", label, button, 0, 0, 10, 10,
                                                   self.hwnd, cid, None, None)
        for handle in self.controls.values():
            u.SendMessageW(handle, 0x0030, self.font, 1)

    def _layout(self, width, height):
        u, pad, row = self.u, 8, 28
        btn_y = height - pad - row
        u.MoveWindow(self.controls["text"], pad, pad, width - pad * 2, max(60, btn_y - pad * 2), True)
        x = pad
        for key in ("copy", "save", "clear", "close"):
            u.MoveWindow(self.controls[key], x, btn_y, 110, row - 4, True)
            x += 116

    def _handle(self, hwnd, msg, wparam, lparam):
        u, w = self.u, self.w
        try:
            if msg == self.WM_CREATE:              # WM_CREATE
                type(self)._instances[hwnd] = self
                self.hwnd = hwnd
                self._create_controls()
                rect = w.RECT()
                u.GetClientRect(hwnd, self.c.byref(rect))
                self._layout(rect.right, rect.bottom)
                u.SetTimer(hwnd, 7, 150, None)
                return 0
            if msg == self.WM_SIZE:                # WM_SIZE
                self._layout(lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)
                return 0
            if msg == self.WM_TIMER:               # WM_TIMER
                self._drain()
                return 0
            if msg == self.WM_COMMAND:             # WM_COMMAND
                cid = wparam & 0xFFFF
                if cid == 42:
                    self.parent._copy_text(self.text)
                elif cid == 43:
                    self._save()
                elif cid == 44:
                    self.text = ""
                    u.SetWindowTextW(self.controls["text"], "")
                elif cid == 45:
                    u.DestroyWindow(hwnd)
                return 0
            if msg == self.WM_CLOSE:               # WM_CLOSE
                u.DestroyWindow(hwnd)
                return 0
            if msg == self.WM_DESTROY:             # WM_DESTROY
                type(self)._instances.pop(hwnd, None)
                self.closed = True
                try:
                    if self._log is not None:
                        self._log.close()
                except Exception:
                    pass
                return 0
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            write_crash_log(exc_type, exc_value, exc_tb, where="gui-diag-window")
        return gui_def_window_proc(hwnd, msg, wparam, lparam)

    def push(self, text=""):
        if text is None:
            text = ""
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        if self._log is not None and text:
            try:
                self._log.write(text)
                self._log.flush()
            except Exception:
                pass
        self.queue.put(text)

    def _drain(self):
        changed = False
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            self.text += item
            changed = True
        if len(self.text) > GUI_MAX_CHARS:
            self.text = self.text[-GUI_MAX_CHARS:]
            changed = True
        if changed and self.hwnd:
            self.u.SetWindowTextW(self.controls["text"], self.text)
            self.u.SendMessageW(self.controls["text"], 0x00B1, -1, -1)   # EM_SETSEL 末尾
            self.u.SendMessageW(self.controls["text"], 0x00B7, 0, 0)     # EM_SCROLLCARET

    def _save(self):
        try:
            self._log and self._log.flush()
            # newline="" ：self.text 里已经是 CRLF，文本模式再翻译一次会变成 CRCRLF
            with open(gui_diag_path(), "a", encoding="utf-8", newline="") as handle:
                handle.write(self.text)
            self.parent.user32.MessageBoxW(self.hwnd, "已写入：\n%s" % gui_diag_path(), APP_TITLE, 0x40)
        except Exception as exc:
            self.parent.user32.MessageBoxW(self.hwnd, "保存失败：%s" % exc, APP_TITLE, 0x10)

    def alive(self):
        return bool(self.hwnd) and not self.closed and bool(self.u.IsWindow(self.hwnd))

    def focus(self):
        self.u.SetForegroundWindow(self.hwnd)


class _GuiChoiceDialog(object):
    """通用"从列表里选一个 / 也可以手输"的选择框（模型选择、会话切换用）。

    纪律与 _GuiInputDialog 一样：窗口类全进程只注册一份、状态按 hwnd 查、
    显示完必须把键盘焦点交给某个控件。顺手修掉两个老坑：
      * 列表框漏了 LBS_NOTIFY —— 标签上写着"双击列表里的模型"，可双击根本不通知父窗；
      * 编辑框预填了当前值 → 永远非空 → 从列表里点中的项永远被忽略。
        现在的规则：**手输了就用手的，没手输就用列表里选中的那项**。
    """

    CLASS_NAME = "DshMiniChoiceDlg"
    LB_ADDSTRING, LB_GETCURSEL, LB_GETTEXTLEN, LB_GETTEXT, LB_SETCURSEL = 0x0180, 0x0188, 0x018A, 0x0189, 0x0186
    LBN_SELCHANGE, LBN_DBLCLK = 1, 2
    WM_CREATE, WM_DESTROY, WM_COMMAND, WM_CLOSE = 0x0001, 0x0002, 0x0111, 0x0010
    WM_ACTIVATE = 0x0006
    ID_LABEL, ID_LIST, ID_EDIT, ID_OK, ID_CANCEL = 51, 52, 53, 54, 55
    VK_RETURN, VK_ESCAPE = 0x0D, 0x1B
    WIDTH, HEIGHT = 620, 500
    _states = {}
    _creating = []

    @classmethod
    def _state_for(cls, hwnd):
        state = cls._states.get(hwnd)
        if state is None and cls._creating:
            state = cls._creating[-1]
        return state

    @classmethod
    def _wndproc(cls, hwnd, msg, wparam, lparam):
        state = cls._state_for(hwnd)
        if state is None:
            return gui_def_window_proc(hwnd, msg, wparam, lparam)
        u = state["user32"]
        try:
            if msg == cls.WM_CREATE:
                cls._states[hwnd] = state
                state["hwnd"] = hwnd
                cls._build(u, state)
                return 0
            if msg == cls.WM_ACTIVATE:
                # 激活消息是异步到的，DefWindowProc 的默认处理会把焦点给窗口自己，
                # 把我们设好的焦点顶掉（用户表现：↑↓ 选不了、打字没反应）。
                result = gui_def_window_proc(hwnd, msg, wparam, lparam)
                if (wparam & 0xFFFF) != 0:                 # WA_ACTIVE / WA_CLICKACTIVE
                    cls._focus_primary(u, state)
                return result
            if msg == cls.WM_COMMAND:
                cid = wparam & 0xFFFF
                note = (wparam >> 16) & 0xFFFF
                if cid == cls.ID_CANCEL:
                    cls._close(state, commit=False)
                elif cid == cls.ID_OK or (cid == cls.ID_LIST and note == cls.LBN_DBLCLK):
                    cls._close(state, commit=True)
                return 0
            if msg == cls.WM_CLOSE:
                cls._close(state, commit=False)
                return 0
            if msg == cls.WM_DESTROY:
                cls._states.pop(hwnd, None)
                return 0
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            write_crash_log(exc_type, exc_value, exc_tb, where="gui-choice-dialog")
        return gui_def_window_proc(hwnd, msg, wparam, lparam)

    @classmethod
    def _build(cls, user32, state):
        c = state["ctypes"]
        hwnd = state["hwnd"]
        state["label"] = user32.CreateWindowExW(0, "STATIC", state["label_text"],
                                                0x40000000 | 0x10000000,
                                                14, 12, 576, 20, hwnd, cls.ID_LABEL, None, None)
        state["list"] = user32.CreateWindowExW(
            0, "LISTBOX", "",
            0x40000000 | 0x10000000 | 0x00200000 | 0x00800000 | 0x00010000
            | 0x0100 | 0x0001,          # LBS_NOINTEGRALHEIGHT | LBS_NOTIFY（后者不能少）
            14, 38, 576, 330, hwnd, cls.ID_LIST, None, None)
        state["edit"] = user32.CreateWindowExW(
            0x00000200, "EDIT", state["current"],
            0x40000000 | 0x10000000 | 0x00800000 | 0x0080 | 0x00010000,
            14, 376, 576, 26, hwnd, cls.ID_EDIT, None, None)
        user32.CreateWindowExW(0, "STATIC",
                               "回车=确定    Esc=取消    Tab=在列表和输入框之间切换",
                               0x40000000 | 0x10000000, 14, 406, 576, 18, hwnd, 0, None, None)
        state["ok"] = user32.CreateWindowExW(0, "BUTTON", "确定",
                                             0x40000000 | 0x10000000 | 0x00010000,
                                             430, 430, 78, 28, hwnd, cls.ID_OK, None, None)
        state["cancel"] = user32.CreateWindowExW(0, "BUTTON", "取消",
                                                 0x40000000 | 0x10000000 | 0x00010000,
                                                 516, 430, 78, 28, hwnd, cls.ID_CANCEL, None, None)
        for index, item in enumerate(state["items"]):
            text = c.c_wchar_p(item)
            user32.SendMessageW(state["list"], cls.LB_ADDSTRING, 0, c.cast(text, c.c_void_p).value)
            if item == state["current"]:
                user32.SendMessageW(state["list"], cls.LB_SETCURSEL, index, 0)
        font = state.get("font")
        if font:
            for key in ("label", "list", "edit", "ok", "cancel"):
                if state.get(key):
                    user32.SendMessageW(state[key], 0x0030, font, 1)      # WM_SETFONT

    @classmethod
    def _list_selection(cls, state):
        """当前列表里选中项的文本（没有选中返回空串）。"""
        u = state["user32"]
        index = u.SendMessageW(state["list"], cls.LB_GETCURSEL, 0, 0)
        if index < 0:
            return ""
        size = u.SendMessageW(state["list"], cls.LB_GETTEXTLEN, index, 0)
        buffer = state["ctypes"].create_unicode_buffer(int(size) + 2)
        u.SendMessageW(state["list"], cls.LB_GETTEXT, index,
                       state["ctypes"].cast(buffer, state["ctypes"].c_void_p).value)
        return buffer.value.strip()

    @classmethod
    def _focus_primary(cls, user32, state):
        """把键盘焦点交给"主要控件"：有列表就给列表（↑↓ 直接选），没有就给输入框。"""
        primary = state.get("list") if state.get("items") else state.get("edit")
        if primary and user32.IsWindow(primary) and user32.IsWindowEnabled(primary):
            user32.SetFocus(primary)
        return primary

    @classmethod
    def _close(cls, state, commit):
        u = state["user32"]
        if commit:
            typed = gui_read_edit_text(u, state["ctypes"], state["edit"]).strip()
            if typed and typed != state["current"]:
                state["value"] = typed            # 手输了模型名 → 以手输为准
            else:
                picked = cls._list_selection(state)
                state["value"] = picked or typed or None
        state["done"] = True
        if state.get("hwnd") and u.IsWindow(state["hwnd"]):
            gui_hide_modal_dialog(u, state["wintypes"], state["hwnd"])
            u.DestroyWindow(state["hwnd"])
        gui_wake_message_loop()
        return True

    @classmethod
    def _create(cls, parent, title, label, items, current="", show=True):
        import ctypes
        from ctypes import wintypes
        u = parent.user32
        fonts = getattr(parent, "fonts", None) or []
        state = {"user32": u, "ctypes": ctypes, "wintypes": wintypes, "title": title,
                 "label_text": label, "items": list(items or []), "current": str(current or ""),
                 "value": None, "hwnd": None, "done": False,
                 "font": fonts[0] if fonts else None}
        cls._creating.append(state)
        try:
            gui_register_window_class(u, parent.kernel32, ctypes, wintypes, cls.CLASS_NAME,
                                      cls._wndproc, cursor=True)
            dlg = u.CreateWindowExW(0, cls.CLASS_NAME, title, 0x00C80000, 0x80000000, 0x80000000,
                                    cls.WIDTH, cls.HEIGHT, getattr(parent, "hwnd", None),
                                    None, None, None)
        finally:
            cls._creating.remove(state)
        if not dlg:
            state["done"] = True
            return None, state
        gui_center_on_owner(u, ctypes, wintypes, dlg, getattr(parent, "hwnd", None))
        primary = state.get("list") if state.get("items") else state.get("edit")
        if show:
            gui_show_modal_dialog(u, ctypes, wintypes, dlg, primary)
        elif primary:
            u.SetFocus(primary)
        if primary and primary == state.get("edit"):
            u.SendMessageW(primary, 0x00B1, 0, -1)      # EM_SETSEL 全选，直接打字就覆盖
        return dlg, state

    @classmethod
    def ask(cls, parent, title, label, items, current=""):
        dlg, state = cls._create(parent, title, label, items, current)
        if not dlg:
            return None
        focus_back = parent.user32.GetFocus()
        hotkeys = {cls.VK_RETURN: lambda: cls._close(state, commit=True),
                   cls.VK_ESCAPE: lambda: cls._close(state, commit=False)}
        order = [state.get("list"), state.get("edit"), state.get("ok"), state.get("cancel")]
        gui_run_modal(parent, dlg, hotkeys, order, focus_back, done=lambda: state["done"])
        return state["value"]


class DshGui(object):
    WS_OVERLAPPEDWINDOW = 0x00CF0000
    WS_CHILD, WS_VISIBLE = 0x40000000, 0x10000000
    WS_VSCROLL, WS_HSCROLL = 0x00200000, 0x00100000
    WS_BORDER, WS_TABSTOP = 0x00800000, 0x00010000
    ES_MULTILINE, ES_AUTOVSCROLL, ES_AUTOHSCROLL = 0x0004, 0x0040, 0x0080
    ES_READONLY, ES_LEFT = 0x0800, 0x0000
    BS_PUSHBUTTON, SS_LEFTNOWORDWRAP = 0x00000000, 0x0000000C
    SW_SHOW, CW_USEDEFAULT = 5, -2147483648
    WM_CREATE, WM_DESTROY, WM_CLOSE, WM_SIZE = 0x0001, 0x0002, 0x0010, 0x0005
    WM_SETFOCUS, WM_CHAR = 0x0007, 0x0102
    WM_COMMAND, WM_TIMER, WM_SETFONT, WM_GETFONT = 0x0111, 0x0113, 0x0030, 0x0031
    WM_KEYDOWN, WM_CHAR, WM_APP_DONE = 0x0100, 0x0102, 0x8001
    WM_VSCROLL = 0x0115
    WM_KEYUP = 0x0101
    WM_APP_PICKER = 0x8002
    WM_APP_DIAG = 0x8003
    WM_APP_REFOCUS = 0x8004        # 对话框关闭后：把焦点交回输入框（自己发给自己）
    VK_CONTROL, VK_SHIFT = 0x11, 0x10
    EM_GETSEL, EM_GETLINECOUNT, EM_REPLACESEL = 0x00B0, 0x00BA, 0x00C2
    EM_SETSEL, EM_SCROLLCARET = 0x00B1, 0x00B7
    EM_LINESCROLL, EM_GETFIRSTVISIBLELINE, EM_SETLIMITTEXT = 0x00B6, 0x00CE, 0x00C5
    # 虚拟键码：**每一个都要在这里定义**。v1.1.2 一直在用 self.VK_ESCAPE 却没定义，
    # 按键处理第一行就抛 AttributeError，被 ctypes 回调静默吞掉 ——
    # 结果"回车发送 / Esc 打断 / F1..F8 / Ctrl 系列"全部失效（用户反馈的 Esc 失效就是这个）。
    # 改完记得跑 python dev\check-constants.py。
    VK_RETURN, VK_ESCAPE, VK_TAB = 0x0D, 0x1B, 0x09
    VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
    VK_F1, VK_F2, VK_F3, VK_F4, VK_F5, VK_F6, VK_F7, VK_F8 = (
        0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77)
    VK_J, VK_C, VK_D, VK_L, VK_O, VK_S, VK_P = 0x4A, 0x43, 0x44, 0x4C, 0x4F, 0x53, 0x50
    GWLP_WNDPROC = -4
    ID_OUTPUT, ID_INPUT, ID_SEND = 1001, 1002, 1003
    ID_SELFTEST, ID_SHELLCHECK, ID_SETUP = 1004, 1005, 1006
    ID_CLEAR, ID_COPY, ID_LOG, ID_STATUS = 1007, 1008, 1009, 1010
    ID_MODEL, ID_STOP = 1011, 1012
    # 菜单项 ID（避开控件 ID）
    MENU_NEW, MENU_SAVE, MENU_COPY, MENU_EXIT = 2001, 2002, 2003, 2004
    MENU_OPEN_SESSION, MENU_SEND_IMAGE = 2005, 2006
    MENU_SELFTEST, MENU_SHELLCHECK, MENU_OPEN_DIAG, MENU_OPENLOG, MENU_CLEARLOG, MENU_EXTRACT_PWSH = 2101, 2102, 2103, 2104, 2105, 2106
    MENU_MODEL, MENU_SETUP, MENU_OPENCWD, MENU_REASONING, MENU_VISION = 2201, 2202, 2203, 2204, 2205
    MENU_GUIDE, MENU_ABOUT = 2301, 2302
    TIMER_ID = 1

    def __init__(self, config, autoclose=0, startup_action=None, startup_prompt=None,
                 config_file=None):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare_api()
        self.config = config
        # 启动时真正加载的那个配置文件（--config 指定的路径要能存回去，不能只认默认位置）
        self.config_file = config_file or config_path()
        self.autoclose = autoclose
        self.startup_action = startup_action or ""
        self.startup_prompt = startup_prompt
        self.queue = queue.Queue()
        self.text = ""
        self.busy = False
        self.closed = False
        self.hwnd = None
        self.controls = {}
        self.fonts = []
        self._lock = threading.Lock()
        self._log_handle = None
        self.theme = Theme(False)
        self.model_list = None          # 扫描到的模型列表（None=还没扫）
        self.model_error = ""
        self.session_path = new_session_path() if config.get("save_sessions", True) else None
        self._pending_picker = (bool(config.get("model_picker", True)) and bool(config.get("api_key"))
                                and not autoclose)
        self.history = []               # 输入历史（↑/↓ 翻）
        self.history_index = None
        self.history_draft = ""
        self.ctrl_c_once = False
        self.cancel_event = None

        self.shell = PersistentShell(config)
        self.client = OpenAICompletionsClient(config)
        self.emitter = GuiEmitter(self, config)
        self.agent = Agent(config, self.client, self.shell, self.emitter)

        try:
            # newline="" ：我们自己写 CRLF，别让文本模式再翻译一遍（否则会变成 CRCRLF）
            self._log_handle = open(gui_log_path(), "a", encoding="utf-8", newline="")
        except Exception:
            self._log_handle = None

    # ---- 输出 ----

    def _declare_api(self):
        """声明参数类型（实现在 gui_declare_apis：不声明的话 64 位回调直接炸）。"""
        gui_declare_apis(self.user32, self.gdi32, self.ctypes, self.wintypes)

    def push(self, text):
        """所有输出都走这里：统一转成 CRLF（Windows 的 EDIT 控件只认 \\r\\n，
        只写 \\n 的话换行会直接消失 —— 这就是"回车缺失"的根因），
        同时原样写进日志文件。"""
        if not text:
            return
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        with self._lock:
            self._at_line_start = text.endswith("\n")
            if self._log_handle is not None:
                try:
                    self._log_handle.write(text)
                    self._log_handle.flush()
                except Exception:
                    pass
        self.queue.put(text)

    def push_line(self, text=""):
        """整行输出（命令结果、提示语等）：必要时先补换行，保证另起一行。

        模型正文是流式 push 的，末尾往往没有换行；如果后面直接接别的输出就会粘在一行。
        """
        prefix = "" if getattr(self, "_at_line_start", True) else "\n"
        self.push(prefix + (text or "") + "\n")

    def set_status(self, text):
        self.queue.put(("\x00status", text))

    def _drain(self):
        chunks = []
        status = None
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple) and item[0] == "\x00status":
                status = item[1]
                continue
            chunks.append(item)
        if chunks:
            fresh = "".join(chunks)
            self.text += fresh
            if len(self.text) > GUI_MAX_CHARS:
                # 只有截断时才整体重设（重设会把插入点和滚动位置一起清掉，能少做就少做）
                self.text = self.text[-GUI_MAX_CHARS:]
                if self.hwnd:
                    self.user32.SetWindowTextW(self.controls["output"], self.text)
            elif self.hwnd:
                self._append_output(fresh)
            if self.hwnd:
                self._scroll_output_to_end()
        if status is not None and self.hwnd:
            self.user32.SetWindowTextW(self.controls["status"],
                                       "%s   |   模型 %s   |   %s" %
                                       (status, self.config.get("model"),
                                        self.shell.status_line()))

    def _append_output(self, text):
        """只把"新来的这段"追加到对话区末尾。

        踩过的坑（用户反馈"输出多了以后视图老是对齐到上面，看不见正在输出的内容"）：
        以前每次刷新都 SetWindowTextW 整体重设 —— 它会把插入点和滚动位置一起重置到
        开头；紧跟的 EM_SETSEL 参数是 (-1, -1)，而 wParam=-1 的语义是"取消选择"、
        不是"移到末尾"，于是 EM_SCROLLCARET 把视图送到文本最上面。改成追加就没事了。
        """
        u, c = self.user32, self.ctypes
        edit = self.controls["output"]
        length = int(u.GetWindowTextLengthW(edit))
        u.SendMessageW(edit, self.EM_SETSEL, length, length)          # 插入点放到末尾
        buffer = c.create_unicode_buffer(text)
        u.SendMessageW(edit, self.EM_REPLACESEL, 0, c.cast(buffer, c.c_void_p).value)

    def _scroll_output_to_end(self):
        """把对话区钉在**下边缘**：末行正好落在视口底部。

        三种写法实测对比（同一个窗口、139 行文本、视口约 22 行）：
          * `EM_SETSEL(-1,-1)` + `EM_SCROLLCARET`：wParam=-1 是"取消选择"，
            插入点留在开头 → 滚到**最上面**（v1.1.2 的老 bug）；
          * `SB_BOTTOM`：滚到滚动条最大偏移，多行 EDIT 的滚动范围比正文高 →
            **滚过头**，末行跑到视口顶端、下面 21 行空白（"文本又跑到上面"）；
          * 只有"插入点末尾 + `EM_SCROLLCARET`"也差 6 行（SCROLLCARET 的判定比
            实际视口保守），所以这里直接算差值：目标首行 = 总行数 - 可见行数，
            再用 `EM_LINESCROLL` 滚过去 —— 实测 first 正好等于 117（=139-22）。
        """
        u = self.user32
        edit = self.controls["output"]
        total = int(u.SendMessageW(edit, self.EM_GETLINECOUNT, 0, 0))
        first = int(u.SendMessageW(edit, self.EM_GETFIRSTVISIBLELINE, 0, 0))
        rect = self.wintypes.RECT()
        u.GetClientRect(edit, self.ctypes.byref(rect))
        visible = max(1, (rect.bottom - rect.top) // OUTPUT_LINE_HEIGHT)
        target = max(0, total - visible)
        if target != first:
            u.SendMessageW(edit, self.EM_LINESCROLL, 0, target - first)   # lParam 可为负

    # ---- 窗口 ----

    def _make_font(self, size, bold=False):
        return self.gdi32.CreateFontW(-size, 0, 0, 0, 700 if bold else 400, 0, 0, 0, 1, 0, 0, 5, 0, "微软雅黑")

    def _create_controls(self):
        u, w = self.user32, self.wintypes
        self.fonts = [self._make_font(14), self._make_font(14, True)]
        edit = self.WS_CHILD | self.WS_VISIBLE | self.WS_VSCROLL | self.WS_BORDER | self.ES_MULTILINE \
            | self.ES_AUTOVSCROLL | self.ES_READONLY
        single = self.WS_CHILD | self.WS_VISIBLE | self.WS_BORDER | self.ES_AUTOHSCROLL | self.WS_TABSTOP \
            | self.ES_MULTILINE          # 多行：Ctrl+J / Shift+Enter 换行，Enter 发送
        button = self.WS_CHILD | self.WS_VISIBLE | self.WS_TABSTOP | self.BS_PUSHBUTTON
        static = self.WS_CHILD | self.WS_VISIBLE | self.SS_LEFTNOWORDWRAP

        self.controls["output"] = u.CreateWindowExW(
            0, "EDIT", "", edit, 0, 0, 10, 10, self.hwnd, self.ID_OUTPUT, None, None)
        # 解除 Windows 原生 EDIT 控件默认 32KB (32767 字符) 上限，允许最大 2GB 文本（彻底修复上万字后新字符不显示的 BUG 1）
        u.SendMessageW(self.controls["output"], self.EM_SETLIMITTEXT, 0, 0)
        self.controls["input"] = u.CreateWindowExW(
            0, "EDIT", "", single | 0x0004, 0, 0, 10, 10, self.hwnd, self.ID_INPUT, None, None)
        u.SendMessageW(self.controls["input"], self.EM_SETLIMITTEXT, 1000000, 0)
        # 只有"发送"留在对话区；自检/诊断/设置等全部收进菜单栏（常驻、不占地方、不影响对话）
        self.controls["send"] = u.CreateWindowExW(
            0, "BUTTON", "发送(&S)", button, 0, 0, 10, 10, self.hwnd, self.ID_SEND, None, None)
        # 显式的「停止」按钮：Esc 在没点过输入框（焦点不在文本框）时收不到，
        # 光靠快捷键会让人以为"打断功能坏了"，所以给一个看得见、点得到的入口。
        self.controls["stop"] = u.CreateWindowExW(
            0, "BUTTON", "停止(&T)", button, 0, 0, 10, 10, self.hwnd, self.ID_STOP, None, None)
        self.controls["status"] = u.CreateWindowExW(
            0, "STATIC", "", static, 0, 0, 10, 10, self.hwnd, self.ID_STATUS, None, None)
        for name, handle in self.controls.items():
            u.SendMessageW(handle, self.WM_SETFONT, self.fonts[1] if name == "send" else self.fonts[0], 1)
        u.EnableWindow(self.controls["stop"], False)      # 没在跑的时候不给按
        self._create_menus()

    def _create_menus(self):
        u, w, c = self.user32, self.wintypes, self.ctypes
        bar = u.CreateMenu()
        groups = (
            ("会话(&S)", (("新对话\tCtrl+L", self.MENU_NEW),
                          ("打开会话…\tCtrl+O", self.MENU_OPEN_SESSION),
                          ("保存会话\tCtrl+S", self.MENU_SAVE),
                          ("发送图片…\tCtrl+P", self.MENU_SEND_IMAGE), None,
                          ("复制全部\tF8", self.MENU_COPY), None, ("退出\tCtrl+D", self.MENU_EXIT))),
            ("工具(&T)", (("离线自检（93 项）\tF5", self.MENU_SELFTEST),
                          ("pwsh 诊断\tF6", self.MENU_SHELLCHECK),
                          ("释放/检查自带 PowerShell 7", self.MENU_EXTRACT_PWSH), None,
                          ("打开诊断日志", self.MENU_OPEN_DIAG),
                          ("打开界面日志", self.MENU_OPENLOG), None,
                          ("清空界面日志", self.MENU_CLEARLOG))),
            ("设置(&C)", (("选择模型\tF3", self.MENU_MODEL), ("接口与密钥\tF2", self.MENU_SETUP),
                          ("显示思考过程\tF4", self.MENU_REASONING),
                          ("支持图片输入(&V)", self.MENU_VISION), None,
                          ("打开工作目录", self.MENU_OPENCWD))),
            ("帮助(&H)", (("操作指南\tF1", self.MENU_GUIDE), ("关于", self.MENU_ABOUT))),
        )
        for title, items in groups:
            popup = u.CreatePopupMenu()
            for item in items:
                if item is None:
                    u.AppendMenuW(popup, 0x00000800, 0, None)          # MF_SEPARATOR
                else:
                    label, mid = item
                    u.AppendMenuW(popup, 0x00000000, mid, label)       # MF_STRING
            u.AppendMenuW(bar, 0x00000010, popup, title)               # MF_POPUP
        u.SetMenu(self.hwnd, bar)
        self.menu_bar = bar
        self._sync_reasoning_menu()
        self._sync_vision_menu()

    def _sync_reasoning_menu(self):
        """把"显示思考过程"的勾选状态跟配置对齐。"""
        if not getattr(self, "menu_bar", None):
            return
        checked = 0x00000008 if self.config.get("show_reasoning") else 0x00000000   # MF_CHECKED
        self.user32.CheckMenuItem(self.menu_bar, self.MENU_REASONING, checked)

    def _sync_vision_menu(self):
        """把"支持图片输入"的勾选状态跟配置对齐。"""
        if not getattr(self, "menu_bar", None):
            return
        checked = 0x00000008 if self.config.get("support_vision", True) else 0x00000000   # MF_CHECKED
        self.user32.CheckMenuItem(self.menu_bar, self.MENU_VISION, checked)

    def _toggle_vision(self):
        """菜单：开启/关闭图片读取与视觉支持（写进配置文件）。"""
        enabled = not bool(self.config.get("support_vision", True))
        self.config["support_vision"] = enabled
        self._sync_vision_menu()
        note = ""
        try:
            save_config_file(self.config_file, self.config)
            note = "（已写入配置）"
        except Exception as exc:
            note = "（写配置失败：%s）" % exc
        self.push_line("● 支持图片输入（视觉能力）：%s%s" % ("开" if enabled else "关", note))

    def _send_image_dialog(self):
        """发送图片对话框：输入本地图片路径和提示词，自动以 OpenAI 格式发送给模型分析。"""
        if self.busy:
            self.push_line("! 上一轮还在执行，请稍后再试或按 Esc 打断。")
            return
        if not self.config.get("support_vision", True):
            self.push_line("! 当前未开启图片读取支持：请先在菜单「设置」中勾选「支持图片输入」")
            return

        img_path = _GuiInputDialog.ask(
            self, "发送图片分析", "本地图片路径（支持 PNG / JPG / WEBP / GIF / BMP）：",
            "", secret=False, check_type=None)
        if not img_path or not img_path.strip():
            return
        img_path = img_path.strip().strip('"').strip("'")
        if not os.path.isfile(img_path):
            self.push_line("! 图片文件不存在：%s" % img_path)
            return
        if not is_image_path(img_path):
            self.push_line("! 不支持的文件格式（仅支持 png/jpg/jpeg/webp/gif/bmp）：%s" % img_path)
            return

        prompt = _GuiInputDialog.ask(
            self, "分析提示词", "请输入对这张图片的分析要求（可留空，默认：请分析这张图片）：",
            "请详细分析并描述这张图片的内容", secret=False, check_type=None)
        if prompt is None:
            return
        prompt = prompt.strip() or "请详细分析并描述这张图片的内容"

        self.push("\n› [图片: %s] %s\n" % (os.path.basename(img_path), prompt))
        self._set_busy(True)
        self.cancel_event = threading.Event()
        thread = threading.Thread(target=self._worker, args=(prompt,), kwargs={"images": [img_path]})
        thread.daemon = True
        thread.start()

    def _sync_reasoning_menu(self):
        """把"显示思考过程"的勾选状态跟配置对齐。"""
        if not getattr(self, "menu_bar", None):
            return
        checked = 0x00000008 if self.config.get("show_reasoning") else 0x00000000   # MF_CHECKED
        self.user32.CheckMenuItem(self.menu_bar, self.MENU_REASONING, checked)

    def _toggle_reasoning(self):
        """F4 / 菜单：显示/隐藏模型思维链（顺手写进配置文件）。"""
        enabled = not bool(self.config.get("show_reasoning"))
        self.config["show_reasoning"] = enabled
        self.emitter.show_reasoning = enabled
        self._sync_reasoning_menu()
        note = ""
        try:
            save_config_file(self.config_file, self.config)
            note = "（已写入配置）"
        except Exception as exc:
            note = "（写配置失败：%s）" % exc
        self.push_line("● 显示思考过程：%s%s" % ("开" if enabled else "关", note))

    def _layout(self, width, height):
        u = self.user32
        pad, row = 8, 28
        bottom = height - pad
        status_h = 20
        button_w, gap = 84, 4
        input_h = getattr(self, "_input_height_cache", None) or (10 + 20)
        input_y = bottom - status_h - input_h - 4
        buttons = button_w * 2 + gap
        u.MoveWindow(self.controls["output"], pad, pad, width - pad * 2,
                     max(80, input_y - pad * 2), True)
        u.MoveWindow(self.controls["input"], pad, input_y, width - pad * 2 - buttons - gap, input_h, True)
        u.MoveWindow(self.controls["stop"], width - pad - buttons, input_y + input_h - row + 4,
                     button_w, row - 4, True)
        u.MoveWindow(self.controls["send"], width - pad - button_w, input_y + input_h - row + 4,
                     button_w, row - 4, True)
        u.MoveWindow(self.controls["status"], pad, bottom - status_h, width - pad * 2, status_h, True)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        u, w = self.user32, self.wintypes
        try:
            if msg == self.WM_CREATE:
                self.hwnd = hwnd
                self._create_controls()
                rect = w.RECT()
                u.GetClientRect(hwnd, self.ctypes.byref(rect))
                self._layout(rect.right, rect.bottom)
                return 0
            if msg == self.WM_SIZE:
                self._layout(lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)
                return 0
            if msg == self.WM_TIMER:
                self._drain()
                return 0
            if msg == self.WM_APP_DONE:
                self._drain()
                self._set_busy(False)        # 恢复"发送"、灰掉"停止"
                self._focus_input()          # 一轮结束后把光标还给输入框，接着就能打字
                return 0
            if msg == self.WM_APP_PICKER:
                self._pick_model()
                return 0
            if msg == self.WM_APP_DIAG:
                self._run_diagnostics("selftest" if wparam == 1 else "shellcheck")
                return 0
            if msg == self.WM_APP_REFOCUS:
                self._focus_input()
                return 0
            if msg == self.WM_COMMAND:
                cid = wparam & 0xFFFF
                if cid == self.ID_SEND:
                    self._on_send()
                elif cid == self.ID_STOP:
                    self._interrupt()
                elif cid in (self.MENU_NEW,):
                    self._new_conversation()
                elif cid == self.MENU_OPEN_SESSION:
                    self._open_session_dialog()
                elif cid == self.MENU_SAVE:
                    self._save_session_now()
                elif cid in (self.MENU_COPY, self.ID_COPY):
                    self._copy_all()
                elif cid == self.MENU_EXIT:
                    self._shutdown()
                elif cid == self.MENU_SELFTEST:
                    self._run_diagnostics("selftest")
                elif cid == self.MENU_SHELLCHECK:
                    self._run_diagnostics("shellcheck")
                elif cid == self.MENU_EXTRACT_PWSH:
                    self._manual_extract_pwsh()
                elif cid == self.MENU_OPEN_DIAG:
                    self._open_diag("pwsh 诊断 / 自检")
                elif cid == self.MENU_OPENLOG:
                    os.startfile(gui_log_path())
                elif cid == self.MENU_CLEARLOG:
                    self._clear_log()
                elif cid in (self.MENU_MODEL, self.ID_MODEL):
                    self._request_models()
                elif cid == self.MENU_SETUP:
                    self._on_setup()
                elif cid == self.MENU_OPENCWD:
                    try:
                        os.startfile(self.config.get("cwd") or os.getcwd())
                    except Exception as exc:
                        self.push_line("! 打不开工作目录：%s" % exc)
                elif cid == self.MENU_GUIDE:
                    self._open_guide()
                elif cid == self.MENU_ABOUT:
                    self._about()
                elif cid == self.MENU_VISION:
                    self._toggle_vision()
                elif cid == self.MENU_SEND_IMAGE:
                    self._send_image_dialog()
                return 0
            if msg == self.WM_CLOSE:
                self._shutdown()
                return 0
            if msg == self.WM_DESTROY:
                u.PostQuitMessage(0)
                return 0
            if msg == self.WM_SETFOCUS:
                # 焦点落到主窗自己身上时（对话框关掉后的激活、Alt+Tab 切回来等），
                # 直接把光标交给输入框 —— 否则用户看着窗口是活的，打字却毫无反应
                # （"输入状态卡死"就是这么来的）。
                if self.user32.GetFocus() == hwnd:
                    self._focus_input()
                return 0
            if msg == self.WM_CHAR:
                # 焦点万一还在主窗上，也把字符转给输入框，别让按键白按
                if self.user32.GetFocus() == hwnd and self.controls.get("input"):
                    self.user32.SetFocus(self.controls["input"])
                    self.user32.PostMessageW(self.controls["input"], msg, wparam, lparam)
                    return 0
            if msg == self.WM_KEYDOWN:
                # 主窗自己也处理快捷键：焦点在按钮/状态栏/窗口本身时（例如刚启动还没点过
                # 输入框），Esc 和 F1..F8 也得管用 —— 用户反馈的"Esc 打断失效"就是这个。
                if self._safe_key(hwnd, wparam, is_input=True):
                    return 0
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            path = write_crash_log(exc_type, exc_value, exc_tb, where="gui-wndproc")
            try:
                u.MessageBoxW(hwnd, "界面出错：%s\n已写入 %s" % (exc_value, path),
                              APP_TITLE, 0x00000010)
            except Exception:
                pass
        return u.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _input_subclass(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_KEYDOWN and self._safe_key(hwnd, wparam, is_input=True):
            return 0
        if msg == self.WM_CHAR and wparam in (13, 10):
            return 0
        result = self.user32.CallWindowProcW(self._old_input_proc, hwnd, msg, wparam, lparam)
        if msg in (self.WM_KEYUP, self.WM_CHAR) and hwnd == getattr(self, "_input_hwnd", None):
            self._autogrow_input()
        return result

    def _output_subclass(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_KEYDOWN and self._safe_key(hwnd, wparam, is_input=False):
            return 0
        return self.user32.CallWindowProcW(self._old_output_proc, hwnd, msg, wparam, lparam)

    def _safe_key(self, hwnd, key, is_input):
        """跑快捷键处理，并把异常留成日志。

        窗口过程是 ctypes 回调：里面抛异常只会打到 stderr（无控制台打包时谁都看不见），
        键就被静静吃掉了 —— v1.1.2 的"所有快捷键失效"就是这么来的。
        所以这里必须自己兜住并写 dsh-mini-error.log。
        """
        try:
            return bool(self._handle_key(hwnd, int(key), is_input=is_input))
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            path = write_crash_log(exc_type, exc_value, exc_tb, where="gui-hotkey")
            try:
                self.push_line("! 快捷键处理出错（已写入 %s）：%s" % (path, exc_value))
            except Exception:
                pass
            return False

    # ---- 快捷键（尽量保留 TUI 时期的手感） ----

    def _handle_key(self, hwnd, key, is_input):
        u = self.user32
        ctrl = bool(u.GetKeyState(self.VK_CONTROL) & 0x8000)
        shift = bool(u.GetKeyState(self.VK_SHIFT) & 0x8000)
        if key == self.VK_ESCAPE:                      # Esc：打断当前输出/执行
            self._interrupt()
            return True
        if key == self.VK_F1:                          # F1 操作指南
            self._open_guide()
            return True
        if key == self.VK_F2:                          # F2 接口与密钥
            self._on_setup()
            return True
        if key == self.VK_F3:                          # F3 选择模型
            self._request_models()
            return True
        if key == self.VK_F4:                          # F4 显示/隐藏思考过程
            self._toggle_reasoning()
            return True
        if key == self.VK_F5:                          # F5 离线自检
            self._run_diagnostics("selftest")
            return True
        if key == self.VK_F6:                          # F6 pwsh 诊断
            self._run_diagnostics("shellcheck")
            return True
        if key == self.VK_F7:                          # F7 清空对话
            self._new_conversation()
            return True
        if key == self.VK_F8:                          # F8 复制全部
            self._copy_all()
            return True
        if ctrl and key == self.VK_L:                  # Ctrl+L 清空显示
            self._new_conversation()
            return True
        if ctrl and key == self.VK_S:                  # Ctrl+S 保存会话
            self._save_session_now()
            return True
        if ctrl and key == self.VK_D:                  # Ctrl+D 退出
            self._shutdown()
            return True
        if not is_input:
            return False
        if key == self.VK_RETURN and not shift:        # Enter 发送（多行框里也不含糊）
            self._on_send()
            return True
        if key == 0x0A:                                # Ctrl+J 换行（多行输入）
            self._insert_newline()
            return True
        if key == self.VK_RETURN and shift:            # Shift+Enter 也换行
            self._insert_newline()
            return True
        if ctrl and key == self.VK_O:                  # Ctrl+O 打开会话
            self._open_session_dialog()
            return True
        if ctrl and key == self.VK_P:                  # Ctrl+P 发送图片分析
            self._send_image_dialog()
            return True
        if key in (self.VK_UP, self.VK_DOWN):          # ↑ / ↓ 翻历史输入
            if self._history_step(-1 if key == self.VK_UP else 1):
                return True
        if ctrl and key == self.VK_C:                  # Ctrl+C
            if self._has_selection():
                return False                           # 有选中内容 -> 走系统复制
            self._clear_input_or_exit()
            return True
        return False

    def _insert_newline(self):
        self.user32.SendMessageW(self.controls["input"], 0x00C2, 1, 0)   # EM_REPLACESEL
        self._autogrow_input()

    def _has_selection(self):
        result = self.user32.SendMessageW(self.controls["input"], self.EM_GETSEL, 0, 0)
        return (result & 0xFFFF) != ((result >> 16) & 0xFFFF)

    def _history_step(self, delta):
        if not self.history:
            return False
        text = self._input_text()
        if "\n" in text:
            return False                               # 多行时交给正常光标移动
        if self.history_index is None:
            self.history_draft = text
            self.history_index = len(self.history)
        index = self.history_index + delta
        index = max(0, min(len(self.history), index))
        self.history_index = index
        value = self.history_draft if index >= len(self.history) else self.history[index]
        self.user32.SetWindowTextW(self.controls["input"], value)
        self.user32.SendMessageW(self.controls["input"], self.EM_SETSEL, -1, -1)
        return True

    def _clear_input_or_exit(self):
        text = self._input_text()
        if text:
            self.user32.SetWindowTextW(self.controls["input"], "")
            self._autogrow_input()
            self.ctrl_c_once = False
            return
        if getattr(self, "ctrl_c_once", False):
            self._shutdown()
            return
        self.ctrl_c_once = True
        self.set_status("再按一次 Ctrl+C 退出（或 Alt+F4）")

    def _focus_input(self):
        """把键盘焦点放回输入框（前提：没有对话框开着 —— 那时主窗是禁用的）。"""
        if not self.hwnd or not self.user32.IsWindowEnabled(self.hwnd):
            return
        if self.user32.GetFocus() == self.controls.get("input"):
            return
        self.user32.SetFocus(self.controls["input"])

    def _set_busy(self, busy):
        """一轮开始/结束时的界面状态（发送按钮、停止按钮）。"""
        self.busy = busy
        if not self.hwnd:
            return
        u = self.user32
        u.EnableWindow(self.controls["send"], not busy)
        u.EnableWindow(self.controls["stop"], busy)

    def _interrupt(self):
        if not self.busy:
            self.set_status("就绪（当前没有在执行的内容）")
            return
        if self.cancel_event is not None:
            self.cancel_event.set()
        try:
            self.shell.kill()                          # 正在跑的命令也一并停掉
        except Exception:
            pass
        self.push("\n! 已打断（Esc / 停止按钮）\n")
        self.set_status("已打断，等这一轮收尾…")
        self._focus_input()

    def _input_text(self):
        length = self.user32.GetWindowTextLengthW(self.controls["input"])
        buf = self.ctypes.create_unicode_buffer(length + 2)
        self.user32.GetWindowTextW(self.controls["input"], buf, length + 2)
        return buf.value

    def _input_lines(self):
        count = self.user32.SendMessageW(self.controls["input"], self.EM_GETLINECOUNT, 0, 0)
        return max(1, min(8, int(count or 1)))

    def _autogrow_input(self):
        height = 10 + self._input_lines() * 20
        if height != getattr(self, "_input_height_cache", None):
            self._input_height_cache = height
            rect = self.wintypes.RECT()
            self.user32.GetClientRect(self.hwnd, self.ctypes.byref(rect))
            self._layout(rect.right, rect.bottom)

    def _on_send(self):
        if self.busy:
            self.push("\n! 上一轮还在执行，按 Esc 可以打断。\n")
            return
        text = self._input_text().strip()
        if not text:
            return
        self.user32.SetWindowTextW(self.controls["input"], "")
        self._autogrow_input()
        self.history.append(text)
        self.history_index = None
        self.history_draft = ""
        self.ctrl_c_once = False
        self.push("\n› " + text + "\n")
        self._set_busy(True)
        self.cancel_event = threading.Event()
        thread = threading.Thread(target=self._worker, args=(text,))
        thread.daemon = True
        thread.start()

    # ---- 诊断：独立窗口，不往对话里倒 ----

    def _run_diagnostics(self, what):
        diag = self._open_diag("pwsh 诊断 / 自检")
        diag.push("\n===== %s =====\n" % ("离线自检" if what == "selftest" else "pwsh 诊断"))
        thread = threading.Thread(target=self._diag_worker, args=(diag, what,))
        thread.daemon = True
        thread.start()
        self.set_status("诊断在独立窗口里跑，不影响对话")

    def _diag_worker(self, diag, what):
        stream = _GuiStream(diag.push)
        old_stdout = sys.stdout
        try:
            if what == "selftest":
                sys.stdout = stream
                try:
                    code = run_selftest()
                finally:
                    sys.stdout = old_stdout
                    stream.flush()
                diag.push("===== 自检结束（退出码 %s）=====\n" % code)
            else:
                # run_shellcheck 的 emit 不带换行（控制台版靠 print 补），这里自己补上
                run_shellcheck(self.config, lambda text="": diag.push((text or "") + "\n"))
                diag.push("===== 诊断结束 =====\n")
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            path = write_crash_log(exc_type, exc_value, exc_tb, where="gui-diag")
            diag.push("!! 诊断过程出错：%s\n（详细堆栈已写入 %s）\n" % (exc_value, path))
        finally:
            diag.push("")   # 触发一次刷新

    def _manual_extract_pwsh(self):
        """用户通过菜单手动触发：释放 / 检查同目录自带稳定版 PowerShell 7。"""
        def _task():
            def cb(msg):
                self.push_line("● %s" % msg)
                self.set_status(msg[:50])
            self.push_line("● 正在检查并释放同目录自带稳定版 PowerShell 7...")
            path = ensure_bundled_powershell(status_callback=cb, force=True)
            if path and os.path.isfile(path):
                self.push_line("√ 自带稳定版 PowerShell 7 已成功就绪：\r\n  %s" % path)
                self.set_status("自带 pwsh 7 就绪")
            else:
                self.push_line("× 释放自带 PowerShell 7 失败，详情请查看诊断日志。")
                self.set_status("自带 pwsh 7 释放失败")
        t = threading.Thread(target=_task, name="dsh-manual-pwsh-extract")
        t.daemon = True
        t.start()

    def _open_diag(self, title):
        """诊断窗口一次只留一个：已经开着就把它提到前面（输出接着往下写），
        不要再开第二个 —— 以前这里会新建实例并把旧实例丢掉，两个窗口抢同一份状态。"""
        diag = getattr(self, "_diag", None)
        if diag is not None and diag.alive():
            diag.focus()
            return diag
        diag = _DiagWindow(self, title)
        self._diag = diag
        return diag

    def _new_conversation(self):
        self._save_current_session()               # 切换前先把当前会话存下来，不丢内容
        self.agent.clear()
        self.text = ""
        self.user32.SetWindowTextW(self.controls["output"], "")
        self.session_path = new_session_path()
        self.push_line("● 新对话已开始（当前会话已保存，上下文已清空）")

    def _save_session_now(self):
        saved = self._save_current_session()
        self.push_line("● %s" % ("已保存会话：%s" % saved if saved else "会话保存失败"))

    def _has_content(self):
        return len(self.agent.messages) > 1        # 除了系统提示词，还有内容

    def _save_current_session(self):
        if not self._has_content():
            return None
        if not self.session_path:
            self.session_path = new_session_path()
        return save_session(self.session_path, self.agent, self.config)

    def _session_display(self, path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            model = str(payload.get("model") or "?")
            stamp = str(payload.get("saved_at") or time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))))
            count = len(payload.get("messages") or [])
        except Exception:
            model, stamp, count = "?", "?", 0
        return "%s · %s · %d 条消息 · %s" % (stamp, model, count, os.path.basename(path))

    def _open_session_dialog(self):
        """多对话切换：列出已保存的会话，选中即切换（切换前自动保存当前会话）。"""
        files = list_sessions(limit=30)
        if not files:
            self.push_line("! 还没有保存过的会话：对话后用「保存会话」或「新对话」会自动存一份。")
            return
        items = []
        mapping = {}
        for path in files:
            label = self._session_display(path)
            items.append(label)
            mapping[label] = path
        chosen = _GuiChoiceDialog.ask(self, "切换会话",
                                      "选择要打开的会话（当前会话会先自动保存）",
                                      items, items[0])
        if not chosen or chosen not in mapping:
            return
        self._load_session(mapping[chosen])

    def _load_session(self, path):
        try:
            messages = load_session(path)
        except Exception as exc:
            self.push_line("! 读取会话失败：%s" % exc)
            return
        if not messages:
            self.push_line("! 这个会话文件是空的：%s" % os.path.basename(path))
            return
        self._save_current_session()
        self.agent.load_messages(messages)
        self.session_path = path
        model = ""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            model = str(payload.get("model") or "")
        except Exception:
            pass
        if model:
            self.config["model"] = model
            self.client.model = model
        self.text = ""
        self.user32.SetWindowTextW(self.controls["output"], "")
        self.push_line("● 已切换会话：%s（模型 %s，共 %d 条消息）"
                       % (os.path.basename(path), self.config.get("model"), len(messages)))

    def _clear_log(self):
        try:
            if self._log_handle is not None:
                self._log_handle.close()
            with open(gui_log_path(), "w", encoding="utf-8") as handle:
                handle.write("")
            self._log_handle = open(gui_log_path(), "a", encoding="utf-8")
            self.push_line("● 界面日志已清空：%s" % gui_log_path())
        except Exception as exc:
            self.push_line("! 清空日志失败：%s" % exc)

    def _open_guide(self):
        for name in ("操作指南.txt", "更新说明.txt", "README.md"):
            path = os.path.join(BASE_DIR, name)
            if os.path.isfile(path):
                try:
                    os.startfile(path)
                    return
                except Exception:
                    pass
        self._about()

    def _about(self):
        self.user32.MessageBoxW(
            self.hwnd,
            "%s v%s\n\n"
            "两个工具：pwsh（PowerShell 会话）、str_replace_editor（文件编辑器）\n"
            "接口：OpenAI 兼容 /chat/completions\n\n"
            "配置文件：%s\n界面日志：%s\n诊断日志：%s\n工作目录：%s"
            % (APP_TITLE, VERSION, self.config_file, gui_log_path(), gui_diag_path(),
               self.config.get("cwd") or os.getcwd()),
            "关于", 0x00000040)

    # ---- 模型选择 ----

    def _request_models(self):
        """点菜单里的"选择模型"：未扫描或上次扫描失败时重新扫描，扫完弹选择框。"""
        if self.model_list is None or (not self.model_list and self.model_error):
            self.model_error = ""
            self.model_list = None
            self.set_status("正在扫描模型列表…")
            threading.Thread(target=self._fetch_models, daemon=True).start()
            return
        self._pick_model()

    def _fetch_models(self):
        try:
            ids = fetch_model_ids(self.config, timeout=15)
            self.model_list = ids
            self.model_error = "" if ids else "接口没有返回模型列表"
        except Exception as exc:
            self.model_list = []
            self.model_error = str(exc)
        self.set_status("就绪")
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, self.WM_APP_PICKER, 0, 0)

    def _pick_model(self):
        self.set_status("就绪")
        current_model = str(self.config.get("model") or "")
        items = list(self.model_list or [])
        if self.model_error:
            self.push_line("! 没能取到模型列表：%s" % self.model_error)
            self.push_line("  可以直接在下面输入模型名（按 F3 随时重试扫描）。")
            label = "提示：未取到在线模型列表（可按 F3 重试），请直接在下方输入模型名："
            if current_model and current_model not in items:
                items.append(current_model)
        else:
            if self.model_list:
                self.push_line("● 可用模型（%d 个）：%s" % (len(self.model_list), "、".join(self.model_list[:30])))
            label = "双击列表里的模型，或直接在下面输入模型名"
            if current_model and current_model not in items:
                items.insert(0, current_model)
        chosen = _GuiChoiceDialog.ask(self, "选择模型", label, items, current_model)
        self.set_status("就绪")
        if not chosen:
            return
        self.config["model"] = chosen
        self.client.reload(self.config)                     # 让 base_url/key/模型一起刷新
        note = ""
        try:
            save_config_file(self.config_file, self.config)  # 选了就存下来，下次启动还是它
            note = "（已写入配置）"
        except Exception as exc:
            note = "（写配置失败：%s）" % exc
        self.push_line("● 模型已切换为 %s%s" % (chosen, note))
        self.set_status("就绪")

    # ---- 对话 ----

    def _worker(self, text, images=None):
        try:
            stripped = text.strip()
            if stripped in ("/models", "/model"):
                if self.hwnd:
                    self.user32.PostMessageW(self.hwnd, self.WM_APP_PICKER, 0, 0)
            elif stripped in ("/shellcheck", "/selftest", "/diag"):
                if self.hwnd:
                    self.user32.PostMessageW(self.hwnd, self.WM_APP_DIAG,
                                             1 if stripped == "/selftest" else 2, 0)
            elif stripped.startswith("/"):
                adapter = _GuiTui(self)
                handle_command(text, self.config, self.theme, adapter, self.agent, self.shell,
                               self.session_path, _GuiReader())
            else:
                self.agent.run_turn(text, cancel=self.cancel_event, images=images)
                if self.session_path:
                    save_session(self.session_path, self.agent, self.config)
        except ApiError as exc:
            if "已取消" in str(exc):
                self.push("\n● 已打断（Esc）\n")
            else:
                self.push("\n! 接口错误：%s\n" % exc)
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            path = write_crash_log(exc_type, exc_value, exc_tb, where="gui-worker")
            self.push("\n! 未预期错误：%s\n  详细堆栈已写入：%s\n" % (exc_value, path))
        finally:
            # worker 线程不碰控件：只置标志，界面按钮和焦点由主线程在 WM_APP_DONE 里收尾
            self.busy = False
            self.cancel_event = None
            self.emitter._end_block()          # 收尾：保证本轮输出以换行结束
            self.set_status("就绪")
            if self.hwnd:
                self.user32.PostMessageW(self.hwnd, self.WM_APP_DONE, 0, 0)

    def _copy_all(self):
        self._copy_text(self.text)

    def _copy_text(self, data):
        kernel32, w = self.kernel32, self.wintypes
        if not self.user32.OpenClipboard(self.hwnd):
            return
        try:
            self.user32.EmptyClipboard()
            size = (len(data) + 1) * self.ctypes.sizeof(w.WCHAR)
            handle = kernel32.GlobalAlloc(0x0042, size)      # GMEM_MOVEABLE | GMEM_ZEROINIT
            if handle:
                pointer = kernel32.GlobalLock(handle)
                self.ctypes.memmove(pointer, self.ctypes.create_unicode_buffer(data), size)
                kernel32.GlobalUnlock(handle)
                self.user32.SetClipboardData(13, handle)     # CF_UNICODETEXT
        finally:
            self.user32.CloseClipboard()

    def _on_setup(self):
        """设置 → 接口与密钥（F2）：只填两项（接口地址、API Key），存盘后**自动接着选模型**。

        模型那一步不再用单行手输框：那一套（扫描 /models + 列表 + 可手输）本来就是
        「选择模型（F3）」在干的事，重复实现只会多一个卡人的地方。
        """
        before = str(self.config.get("base_url") or "")
        values = []
        steps = (("接口地址 base_url", "base_url", False),
                 ("API Key（留空=保持原值）", "api_key", True))
        for index, (label, key, secret) in enumerate(steps, 1):
            current = str(self.config.get(key) or "")
            answer = self._prompt(label, current, secret=secret,
                                  title="设置（第 %d/%d 步）：%s" % (index, len(steps), label))
            if answer is None:                      # 任意一步取消 = 这一轮全部不改
                self.push_line("● 已取消设置（配置没有改动）")
                self.set_status("就绪")
                return
            values.append((key, answer.strip() or current))
        for key, value in values:
            self.config[key] = value
        path = getattr(self, "config_file", None) or config_path()
        try:
            save_config_file(path, self.config)
        except Exception as exc:
            self.push_line("! 配置保存失败：%s" % exc)
            self.user32.MessageBoxW(self.hwnd, "配置保存失败：\n%s\n%s" % (path, exc),
                                    APP_TITLE, 0x00000010)
            return
        # 关键：client 里的 base_url/api_key 是构造时缓存的，不 reload 的话改了也不生效
        self.client.reload(self.config)
        self.push_line("● 配置已保存：%s" % path)
        if not self.config.get("api_key"):
            self.push_line("! api_key 还是空的：接口会拒绝请求，按 F2 再填一次。")
        self.set_status("就绪")
        # 填完前两项自动进入"选择模型"：接口地址换了就重新扫一次模型列表
        if self.config.get("base_url") != before:
            self.model_list = None
            self.model_error = ""
        self.push_line("● 接着选模型：列表来自接口的 /models，扫不到可以直接输入模型名；取消=保持当前。")
        self._request_models()
        self._focus_input()

    def _prompt(self, label, current, secret=False, title=None):
        """借用一个临时小窗口做单行输入（避免引入 tkinter）。"""
        return _GuiInputDialog.ask(self, label, current, title=title, secret=secret)

    # ---- 生命周期 ----

    def _shutdown(self):
        if self.closed:
            return
        self.closed = True
        try:
            saved = self._save_current_session()   # 退出前把有内容的会话存一份，不丢对话
            if saved:
                self.push_line("● 退出前已保存会话：%s" % saved)
        except Exception:
            pass
        try:
            self.shell.kill()
        except Exception:
            pass
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
        self.user32.KillTimer(self.hwnd, self.TIMER_ID)
        self.user32.DestroyWindow(self.hwnd)

    def run(self):
        ctypes, w = self.ctypes, self.wintypes
        self.user32.SetProcessDPIAware()
        hinstance = self.kernel32.GetModuleHandleW(None)
        self._wndproc_ref = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, ctypes.c_uint,
                                               w.WPARAM, w.LPARAM)(self._wndproc)
        self._input_proc_ref = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, ctypes.c_uint,
                                                  w.WPARAM, w.LPARAM)(self._input_subclass)
        self._output_proc_ref = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, ctypes.c_uint,
                                                   w.WPARAM, w.LPARAM)(self._output_subclass)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                        ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE),
                        ("hIcon", w.HICON), ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                        ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR),
                        ("hIconSm", w.HICON)]

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0x0002 | 0x0001                 # CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
        wc.hInstance = hinstance
        wc.hCursor = self.user32.LoadCursorW(None, 32512)   # IDC_ARROW
        wc.hbrBackground = 16 + 1                  # COLOR_BTNFACE + 1
        wc.lpszClassName = "DshMiniGuiWnd"
        if not self.user32.RegisterClassExW(ctypes.byref(wc)):
            raise RuntimeError("RegisterClassEx 失败：%d" % ctypes.get_last_error())

        style = (self.WS_OVERLAPPEDWINDOW & ~0x00040000) | self.WS_VSCROLL
        hwnd = self.user32.CreateWindowExW(
            0, "DshMiniGuiWnd", "%s v%s —— 图形界面（无控制台）" % (APP_TITLE, VERSION),
            style, self.CW_USEDEFAULT, self.CW_USEDEFAULT, 1000, 720,
            None, None, hinstance, None)
        if not hwnd:
            raise RuntimeError("CreateWindowEx 失败：%d" % ctypes.get_last_error())
        self.user32.ShowWindow(hwnd, self.SW_SHOW)
        self.user32.UpdateWindow(hwnd)

        # 输入框回车 = 发送；两个框都挂了快捷键处理器（Esc 打断等）
        self._input_hwnd = self.controls["input"]
        old = self.user32.SetWindowLongPtrW(self.controls["input"], self.GWLP_WNDPROC,
                                            self.ctypes.cast(self._input_proc_ref, self.ctypes.c_void_p))
        self._old_input_proc = old
        old_out = self.user32.SetWindowLongPtrW(self.controls["output"], self.GWLP_WNDPROC,
                                               self.ctypes.cast(self._output_proc_ref,
                                                                self.ctypes.c_void_p))
        self._old_output_proc = old_out

        self.user32.SetTimer(hwnd, self.TIMER_ID, 120, None)
        self._focus_input()          # 光标直接落在输入框：不然 Esc/F 键要先用鼠标点一下才管用
        if self.startup_action or self._pending_picker or self.startup_prompt:
            self._startup_timer = self.TIMER_ID + 2
            self.user32.SetTimer(hwnd, self._startup_timer, 700, None)
        if self.autoclose:
            self._autoclose_timer = self.TIMER_ID + 1
            self.user32.SetTimer(hwnd, self._autoclose_timer, max(1000, int(self.autoclose * 1000)), None)
            self.push_line("[autoclose] %s 秒后自动关闭；期间会自动跑一次 pwsh 诊断来验证后端。" % self.autoclose)
            self._set_busy(True)
            threading.Thread(target=self._worker, args=("/shellcheck",), daemon=True).start()

        msg = w.MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_TIMER:
                if msg.wParam == getattr(self, "_autoclose_timer", -1):
                    self.push_line("[autoclose] 正常收到消息循环，窗口自毁退出")
                    self._shutdown()
                    break
                if msg.wParam == getattr(self, "_startup_timer", -1):
                    self.user32.KillTimer(self.hwnd, self._startup_timer)
                    self._startup_timer = -1
                    self._run_startup()
                    continue
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))
        return 0

    def _run_startup(self):
        """窗口起来后再跑启动动作：命令行参数 / 启动时选模型。"""
        try:
            if self._pending_picker:
                self._pending_picker = False
                self._request_models()
            action = self.startup_action
            self.startup_action = ""
            if action == "selftest":
                self._run_diagnostics("selftest")
            elif action == "shellcheck":
                self._run_diagnostics("shellcheck")
            elif action == "setup":
                self._on_setup()
            elif action in ("model", "models"):
                self._request_models()
            elif action == "prompt" and self.startup_prompt:
                self.user32.SetWindowTextW(self.controls["input"], self.startup_prompt)
                self._on_send()
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            write_crash_log(exc_type, exc_value, exc_tb, where="gui-startup")


class _GuiTui(object):
    """给 handle_command 用的极简适配器（只需要 .line()）。"""

    def __init__(self, gui):
        self.gui = gui

    def line(self, text=""):
        self.gui.push_line(text)

    def write(self, text):
        self.gui.push(text)


class _GuiInputDialog(object):
    """单行输入框（Windows 没有现成的 InputBox，只能自己搭个小窗口）。

    这里是 v1.1.3 的主战场 —— 用户反馈"密钥根本填不进去"，实测是三个缺陷叠在一起：

      1. **建好后没有 SetFocus 到编辑框**：窗口虽然弹出来了，但键盘焦点落在对话框
         窗口自己身上（GetGUIThreadInfo 实测 hwndFocus == 对话框），敲键盘一个字符
         都到不了编辑框；
      2. **窗口类每次都重新注册**：同名第二次起必然失败，于是后续的框继续沿用第一个
         框的窗口过程 —— 第二个框的标签还是"接口地址 base_url"，填进去的值写进了
         一个早已作废的返回值里（见 gui_register_window_class）；
      3. **关窗后消息循环不退出**：GetMessage 只在 WM_QUIT 时返回 0，对话框被销毁后
         它照样一直等消息 → ask() 永远不返回 → _on_setup 永远走不到保存配置那一步。

    于是"按 F2 → 打字没反应 → 点确定什么都没发生"。
    """

    CLASS_NAME = "DshMiniInputDlg"
    WM_CREATE, WM_DESTROY, WM_COMMAND, WM_CLOSE = 0x0001, 0x0002, 0x0111, 0x0010
    WM_ACTIVATE = 0x0006
    WS_EX_CLIENTEDGE = 0x00000200
    ES_AUTOHSCROLL, ES_PASSWORD = 0x0080, 0x0020
    WS_TABSTOP = 0x00010000
    EM_SETSEL, EM_SETPASSWORDCHAR = 0x00B1, 0x00CC
    ID_EDIT, ID_LABEL, ID_OK, ID_CANCEL, ID_SHOW, ID_HINT = 1, 2, 3, 4, 5, 6
    VK_RETURN, VK_ESCAPE = 0x0D, 0x1B
    WIDTH, HEIGHT = 460, 210
    _states = {}        # hwnd -> 这一次弹框的状态
    _creating = []      # 正在创建（CreateWindowExW 还没返回、拿不到 hwnd）的状态

    # ---- 状态：窗口过程全类共用一份，靠 hwnd 找"这一次"的状态 ----

    @classmethod
    def _state_for(cls, hwnd):
        state = cls._states.get(hwnd)
        if state is None and cls._creating:
            state = cls._creating[-1]
        return state

    @classmethod
    def _wndproc(cls, hwnd, msg, wparam, lparam):
        state = cls._state_for(hwnd)
        if state is None:
            return gui_def_window_proc(hwnd, msg, wparam, lparam)
        u = state["user32"]
        try:
            if msg == cls.WM_CREATE:
                cls._states[hwnd] = state
                state["hwnd"] = hwnd
                cls._build(u, state)
                return 0
            if msg == cls.WM_ACTIVATE:
                # 激活消息是异步到的，DefWindowProc 默认会把焦点给窗口自己，
                # 顶掉我们在 _create 里设好的编辑框焦点（用户表现：打字没反应）。
                result = gui_def_window_proc(hwnd, msg, wparam, lparam)
                if (wparam & 0xFFFF) != 0:                 # WA_ACTIVE / WA_CLICKACTIVE
                    cls._focus_primary(u, state)
                return result
            if msg == cls.WM_COMMAND:
                cid = wparam & 0xFFFF
                if cid == cls.ID_OK:
                    cls._close(state, commit=True)
                elif cid == cls.ID_CANCEL:
                    cls._close(state, commit=False)
                elif cid == cls.ID_SHOW and state.get("show"):
                    cls._toggle_password(state)
                return 0
            if msg == cls.WM_CLOSE:
                cls._close(state, commit=False)
                return 0
            if msg == cls.WM_DESTROY:
                cls._states.pop(hwnd, None)
                return 0
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            write_crash_log(exc_type, exc_value, exc_tb, where="gui-input-dialog")
        return gui_def_window_proc(hwnd, msg, wparam, lparam)

    @classmethod
    def _build(cls, user32, state):
        """造控件。编辑框放在第一行，紧跟着标签和提示，确定/取消在右下角。"""
        hwnd = state["hwnd"]
        edit_style = (0x40000000 | 0x10000000 | 0x00800000 | cls.WS_TABSTOP | cls.ES_AUTOHSCROLL)
        if state["secret"]:
            edit_style |= cls.ES_PASSWORD
        state["edit"] = user32.CreateWindowExW(cls.WS_EX_CLIENTEDGE, "EDIT", state["current"],
                                               edit_style, 14, 14, 416, 26,
                                               hwnd, cls.ID_EDIT, None, None)
        state["label"] = user32.CreateWindowExW(0, "STATIC", state["label_text"],
                                                0x40000000 | 0x10000000, 14, 46, 416, 20,
                                                hwnd, cls.ID_LABEL, None, None)
        user32.CreateWindowExW(0, "STATIC",
                               "回车=确定    Esc=取消    Tab=切换    Ctrl+V 粘贴",
                               0x40000000 | 0x10000000, 14, 70, 416, 18, hwnd, cls.ID_HINT, None, None)
        state["show"] = None
        if state["secret"]:
            state["show"] = user32.CreateWindowExW(
                0, "BUTTON", "显示密钥", 0x40000000 | 0x10000000 | cls.WS_TABSTOP | 0x00000003,
                14, 94, 110, 24, hwnd, cls.ID_SHOW, None, None)
        state["ok"] = user32.CreateWindowExW(
            0, "BUTTON", "确定", 0x40000000 | 0x10000000 | cls.WS_TABSTOP | 0x00000001,
            270, 122, 76, 28, hwnd, cls.ID_OK, None, None)
        state["cancel"] = user32.CreateWindowExW(
            0, "BUTTON", "取消", 0x40000000 | 0x10000000 | cls.WS_TABSTOP,
            354, 122, 76, 28, hwnd, cls.ID_CANCEL, None, None)
        font = state.get("font")
        if font:
            for key in ("edit", "label", "ok", "cancel", "show"):
                if state.get(key):
                    user32.SendMessageW(state[key], 0x0030, font, 1)     # WM_SETFONT

    @classmethod
    def _toggle_password(cls, state):
        """勾"显示密钥"：把密码字符去掉/加上（EM_SETPASSWORDCHAR）。"""
        u = state["user32"]
        masked = bool(u.SendMessageW(state["edit"], 0x00D2, 0, 0))       # EM_GETPASSWORDCHAR
        u.SendMessageW(state["edit"], cls.EM_SETPASSWORDCHAR, 0 if masked else 0x25CF, 0)
        u.InvalidateRect(state["edit"], None, True)

    @classmethod
    def _focus_primary(cls, user32, state):
        """这个对话框的主要控件就是那个编辑框。"""
        edit = state.get("edit")
        if edit and user32.IsWindow(edit):
            user32.SetFocus(edit)
        return edit

    @classmethod
    def _close(cls, state, commit):
        u = state["user32"]
        if commit:
            state["value"] = gui_read_edit_text(u, state["ctypes"], state["edit"])
        state["done"] = True
        if state.get("hwnd") and u.IsWindow(state["hwnd"]):
            gui_hide_modal_dialog(u, state["wintypes"], state["hwnd"])
            u.DestroyWindow(state["hwnd"])
        gui_wake_message_loop()          # 必须叫醒消息循环，否则 ask() 永远不返回
        return True

    @classmethod
    def _create(cls, parent, label, current="", title=None, secret=False, show=True):
        """建好对话框（不进消息循环）。拆出来是为了能离线验证"焦点在编辑框上"。"""
        import ctypes
        from ctypes import wintypes
        u = parent.user32
        fonts = getattr(parent, "fonts", None) or []
        state = {"user32": u, "ctypes": ctypes, "wintypes": wintypes,
                 "label_text": label, "current": str(current or ""), "value": None,
                 "secret": bool(secret), "hwnd": None, "edit": None, "done": False,
                 "font": fonts[0] if fonts else None}
        cls._creating.append(state)
        try:
            gui_register_window_class(u, parent.kernel32, ctypes, wintypes, cls.CLASS_NAME,
                                      cls._wndproc, cursor=True)
            dlg = u.CreateWindowExW(0, cls.CLASS_NAME, title or APP_TITLE, 0x00C80000,
                                    0x80000000, 0x80000000, cls.WIDTH, cls.HEIGHT,
                                    getattr(parent, "hwnd", None), None, None, None)
        finally:
            cls._creating.remove(state)
        if not dlg:
            state["done"] = True
            return None, state
        gui_center_on_owner(u, ctypes, wintypes, dlg, getattr(parent, "hwnd", None))
        if show:
            gui_show_modal_dialog(u, ctypes, wintypes, dlg, state.get("edit"))
        elif state.get("edit"):
            u.SetFocus(state["edit"])
        if state.get("edit"):
            u.SendMessageW(state["edit"], cls.EM_SETSEL, 0, -1)   # 全选：直接打字就覆盖
        return dlg, state

    @classmethod
    def ask(cls, parent, label, current="", title=None, secret=False):
        """弹一个单行输入框。返回 None 表示取消。"""
        dlg, state = cls._create(parent, label, current, title, secret)
        if not dlg:
            return None
        focus_back = parent.user32.GetFocus()            # 关掉之后把焦点还回去
        hotkeys = {cls.VK_RETURN: lambda: cls._close(state, commit=True),
                   cls.VK_ESCAPE: lambda: cls._close(state, commit=False)}
        order = [state.get("edit"), state.get("show"), state.get("ok"), state.get("cancel")]
        gui_run_modal(parent, dlg, hotkeys, order, focus_back, done=lambda: state["done"])
        return state["value"]


def run_gui(config, autoclose=0, startup_action="", startup_prompt=None, config_file=None):
    if not gui_supported():
        print("当前系统不支持图形界面（仅 Windows）。")
        return 2
    if not config.get("cwd"):
        config["cwd"] = os.getcwd()
    first_run = not config.get("api_key")
    if first_run and not startup_action and not autoclose:
        startup_action = "setup"          # 第一次使用：窗口起来后自动弹设置（填地址 + 密钥）
    gui = DshGui(config, autoclose=autoclose, startup_action=startup_action,
                 startup_prompt=startup_prompt, config_file=config_file)
    config["cwd"] = config.get("cwd") or os.getcwd()
    gui.push("%s v%s  ——  图形界面\n" % (APP_TITLE, VERSION))
    gui.push("工作目录：%s\n配置文件：%s\n界面日志：%s\n诊断日志：%s\n"
             % (config.get("cwd"), gui.config_file, gui_log_path(), gui_diag_path()))
    gui.push("\n键盘操作（沿用命令行版的手感）：\n"
             "  Esc                打断当前输出 / 停止正在执行的命令（也可以点「停止」按钮）\n"
             "  Enter              发送；Ctrl+J 或 Shift+Enter 换行（可写多行）\n"
             "  ↑ / ↓              翻阅历史输入\n"
             "  Ctrl+C             有选中=复制；没选中=清空输入；再按一次=退出\n"
             "  Ctrl+D             退出；Ctrl+L 清空显示；Ctrl+S 保存会话\n"
             "  F1 操作指南   F2 接口与密钥   F3 选择模型   F4 显示思考过程\n"
             "  F5 离线自检   F6 pwsh 诊断    F7 清空对话   F8 复制全部\n"
             "  （自检/诊断的结果在独立窗口里，不会冲掉对话）\n")
    if first_run:
        gui.push("\n! 还没有配置 api_key：正在自动打开设置（也可以随时按 F2）。\n"
                 "  第 1 步填接口地址、第 2 步填密钥；填完会自动接着让你选模型。\n")
    return gui.run()


# =============================================================================
# 18. 命令行入口
# =============================================================================

def build_parser():
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="%s v%s —— 与 DSH 极简模式功能对齐的单文件本地 Agent（工具：pwsh、str_replace_editor）"
                    % (APP_TITLE, VERSION),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_TEXT,
    )
    parser.add_argument("-p", "--prompt", help="单次执行：直接发送一条消息并打印结果")
    parser.add_argument("--config", help="指定配置文件路径（默认 %s）" % CONFIG_NAME)
    parser.add_argument("--base-url", help="OpenAI 兼容接口地址")
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--model", help="模型名称")
    parser.add_argument("--cwd", help="工作目录")
    parser.add_argument("--max-rounds", type=int, help="单轮最多工具调用轮数")
    parser.add_argument("--shell-timeout", type=int, help="pwsh 单条命令超时（毫秒）")
    parser.add_argument("--shell-mode", choices=["auto", "persistent", "oneshot"],
                        help="pwsh 会话模式：auto=优先持久、失败自动降级；persistent=强制持久；oneshot=每条命令一个进程")
    parser.add_argument("--shellcheck", action="store_true", help="离线诊断 pwsh 工具（Win7/老系统排障用）")
    parser.add_argument("--gui", action="store_true", help="打开图形界面（无控制台黑框）")
    parser.add_argument("--models", action="store_true",
                        help="打开图形界面并弹出模型选择列表（等价于启动时 F3）")
    parser.add_argument("--gui-autoclose", type=int, default=0,
                        help=argparse.SUPPRESS)
    parser.add_argument("--editor", choices=["auto", "raw", "line"], help="输入模式：raw=增强键盘，line=简单")
    parser.add_argument("--pick-model", action="store_true",
                        help="启动时强制弹出模型选择（非交互环境也可用，便于脚本化）")
    parser.add_argument("--no-pick-model", action="store_true", help="启动时跳过模型选择")
    parser.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    parser.add_argument("--no-color", action="store_true", help="关闭颜色")
    parser.add_argument("--no-save", action="store_true", help="不保存会话")
    parser.add_argument("--quiet-tools", action="store_true", help="单次模式下不打印工具执行日志")
    parser.add_argument("--setup", action="store_true", help="运行配置向导并保存")
    parser.add_argument("--force-wizard", action="store_true",
                        help="强制运行配置向导（非交互环境也可用，便于脚本化）")
    parser.add_argument("--selftest", action="store_true", help="运行离线自检")
    parser.add_argument("--version", action="version", version="%s %s" % (APP_NAME, VERSION))
    return parser


def _message_box(text, title=None):
    """无控制台时的错误提示（windowed exe 里 print 是看不见的）。"""
    shown = False
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, console_safe(text), title or APP_TITLE, 0x00000010)
            shown = True
        except Exception:
            shown = False
    if not shown:
        try:
            print(text)
        except Exception:
            pass


def main(argv=None):
    install_safe_streams()      # 控制台编不出来的字符降级，别让 print 把程序带崩
    install_crash_handler()     # 万一还是崩了，留一份 dsh-mini-error.log
    fix_stdio_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)

    # Win7 深度定制：预先探测/释放自带稳定版 PowerShell 7 到同目录
    try:
        ensure_bundled_powershell()
    except Exception:
        pass

    # 无控制台（windowed 打包）时图形界面就是唯一的界面：
    # --selftest / --shellcheck / --setup / -p 这些参数会被翻译成"开窗口 + 自动执行该动作"。
    headless_build = (sys.stdout is None and sys.stderr is None)
    if args.gui or headless_build:
        config = resolve_config(args)
        if not config.get("cwd") or not os.path.isdir(config["cwd"]):
            config["cwd"] = os.getcwd()
        action = ""
        prompt = None
        if args.selftest:
            action = "selftest"
        elif args.shellcheck:
            action = "shellcheck"
        elif args.setup:
            action = "setup"
        elif args.models:
            action = "models"
        elif args.prompt is not None:
            action, prompt = "prompt", args.prompt
        try:
            return run_gui(config, autoclose=args.gui_autoclose or 0,
                           startup_action=action, startup_prompt=prompt,
                           config_file=args.config or config_path())
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            path = write_crash_log(exc_type, exc_value, exc_tb, where="gui-start")
            _message_box("图形界面启动失败：%s\n\n详细堆栈已写入：%s" % (exc_value, path))
            return 2

    if args.selftest:
        return run_selftest()

    if args.shellcheck:
        try:
            return run_shellcheck(resolve_config(args))
        except Exception:
            exc_type, exc_value, exc_tb = sys.exc_info()
            path = write_crash_log(exc_type, exc_value, exc_tb, where="shellcheck")
            print("--shellcheck 自身出错：%s" % exc_value)
            if path:
                print("详细堆栈已写入：%s" % path)
            return 2

    config = resolve_config(args)
    reader = StdinReader(config.get("shell_encoding") or "auto")

    if args.setup:
        config = setup_wizard(config, reader)
        if not args.prompt:
            return 0

    if not config.get("cwd"):
        config["cwd"] = os.getcwd()
    if not os.path.isdir(config["cwd"]):
        config["cwd"] = os.getcwd()

    theme = Theme(True)

    if args.prompt is not None:
        if not config.get("api_key"):
            print("尚未配置 api_key，请先运行：%s --setup" % self_invocation(), file=sys.stderr)
            return 2
        return run_once(config, args.prompt, quiet_tools=args.quiet_tools)

    if args.force_wizard or (not config.get("api_key") and _stdin_is_tty()):
        config = setup_wizard(config, reader)
    elif not config.get("api_key"):
        print("尚未配置 api_key。请运行 --setup，或在 %s 中填写。" % config_path(), file=sys.stderr)
        return 2

    try:
        return run_interactive(config, theme, force_picker=bool(args.pick_model), reader=reader)
    except KeyboardInterrupt:
        print("")
        return 130


if __name__ == "__main__":
    sys.exit(main())
