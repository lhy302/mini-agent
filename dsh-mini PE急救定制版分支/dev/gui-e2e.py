# -*- coding: utf-8 -*-
"""dsh-mini GUI 端到端测试（设置流程 / 思考过程 / 打断 / 输出滚动）。

为什么必须真开窗口测：这一串问题（焦点没给编辑框、窗口类重复注册、消息循环不退出、
视图跳到文章开头）只有真的走一遍消息队列才暴露得出来，离线自检抓不到。

用法（在 dev 目录下）：

    python gui-e2e.py                          # 测源码：用当前 Python 跑 dev/dsh-mini.py
    python gui-e2e.py --exe ..\\dsh-mini.exe    # 测打包产物（更接近用户环境）
    python gui-e2e.py --keep                   # 保留临时目录，排错用

测试完全不联网也不碰用户配置：临时目录里放一份脚本/exe + 自己的配置，
接口指向本进程内的**假网关**（FakeGateway，见下）。

判定方式（都在进程外可观察）：
  * 焦点用 GetGUIThreadInfo 实测；
  * 输入用 PostMessage 把 WM_CHAR / WM_KEYDOWN 投进目标线程的消息队列
    （真实键盘要求窗口在前台，自动化拿不到前台，但走的是同一条队列、同一套代码路径）；
  * 结果以落盘的 dsh-mini.config.json / dsh-mini-gui.log 和假网关收到的请求为准。

退出码 0 = 全部通过。
"""
import argparse
import ctypes
import ctypes.wintypes as wintypes
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# 必须声明 DPI 感知：否则本进程量到的控件尺寸会被系统按缩放虚化
# （实测 574px 的真实客户区被报成 459px，据此算"可见行数"就会得出错误结论）
user32.SetProcessDPIAware()

user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindowEnabled.argtypes = [wintypes.HWND]
user32.IsWindowEnabled.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL

WM_COMMAND, WM_CHAR, WM_KEYDOWN, VK_RETURN, VK_ESCAPE = 0x0111, 0x0102, 0x0100, 0x0D, 0x1B
GWL_ID, GWL_STYLE, GWL_EXSTYLE = -12, -16, -20
WS_EX_TOPMOST = 0x00000008
WS_TABSTOP = 0x00010000
EM_GETLINECOUNT, EM_GETFIRSTVISIBLELINE = 0x00BA, 0x00CE
MENU_SETUP, MENU_MODEL, MENU_REASONING = 2202, 2201, 2204
ID_STOP = 1012
# 两个对话框的控件 ID 不一样（见 dsh-mini.py 里各自的 ID_* 常量）
INPUT_IDS = {"edit": 1, "label": 2, "ok": 3, "cancel": 4, "show": 5}
CHOICE_IDS = {"edit": 53, "label": 51, "list": 52, "ok": 54, "cancel": 55}
MAIN_IDS = {"output": 1001, "input": 1002, "send": 1003, "status": 1010, "stop": 1012}


# ============================ 假网关（本机 SSE） ============================

class _GatewayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    # ---- GET /v1/models ----
    def do_GET(self):
        gateway = self.server.gateway
        gateway.record(self.headers.get("Authorization"))
        if not self.path.startswith("/v1/models"):
            self.send_error(404)
            return
        body = json.dumps({"object": "list",
                           "data": [{"id": "fake-reasoner"}, {"id": "fake-chat"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- POST /v1/chat/completions ----
    def do_POST(self):
        gateway = self.server.gateway
        gateway.record(self.headers.get("Authorization"))
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        except Exception:
            payload = {}
        gateway.last_payload = payload
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            if gateway.mode == "endless":
                self._stream_endless()
            elif gateway.mode == "long":
                self._stream_long()
            else:
                self._stream_reasoning()
        except Exception:
            gateway.stream_broken = True        # 客户端断开（打断成功）会走到这里

    # ---- 各种流 ----
    def _chunk(self, delta, finish=None):
        choice = {"index": 0, "delta": delta}
        if finish:
            choice["finish_reason"] = finish
        payload = {"choices": [choice]}
        self.wfile.write(("data: %s\n\n" % json.dumps(payload, ensure_ascii=False)).encode("utf-8"))
        self.wfile.flush()

    def _done(self):
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _stream_reasoning(self):
        gateway = self.server.gateway
        for index in range(1, 4):
            gateway.reasoning_sent += 1
            self._chunk({"reasoning_content": "思考第%d步：先想一下。" % index})
            time.sleep(0.05)
        self._chunk({"content": "这是最终回答（fake-gateway）。"})
        self._chunk({}, finish="stop")
        self._done()

    def _stream_long(self):
        for index in range(1, 121):
            self._chunk({"content": "输出行 %03d：AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n" % index})
            time.sleep(0.01)
        self._chunk({}, finish="stop")
        self._done()

    def _stream_endless(self):
        gateway = self.server.gateway
        for index in range(1, 400):
            gateway.reasoning_sent += 1
            self._chunk({"reasoning_content": "还在想 %d…" % index})
            time.sleep(0.05)


class FakeGateway(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, mode="reasoning"):
        http.server.HTTPServer.__init__(self, ("127.0.0.1", 0), _GatewayHandler)
        self.gateway = self          # 处理函数里通过 self.server.gateway 拿状态
        self.mode = mode
        self.auth = []
        self.requests = 0
        self.reasoning_sent = 0
        self.stream_broken = False
        self.last_payload = None
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def record(self, authorization):
        with self._lock:
            self.requests += 1
            self.auth.append(authorization or "")

    @property
    def base_url(self):
        return "http://127.0.0.1:%d/v1" % self.server_address[1]

    def close(self):
        try:
            self.shutdown()
            self.server_close()
        except Exception:
            pass


# ============================ Win32 小工具 ============================

class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


TH32CS_SNAPPROCESS = 0x00000002


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def process_family(root_pid):
    """启动器 PID + 它的所有子孙进程 PID。

    PyInstaller onefile 是"启动器 + 子进程"两个进程，窗口属于子进程
    （开发文档 §9.6/§9.7）：只按启动器 PID 找窗口永远找不到。
    """
    family = {int(root_pid)}
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return family
    try:
        parents = {}
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        grown = True
        while grown:
            grown = False
            for pid, parent in parents.items():
                if parent in family and pid not in family:
                    family.add(pid)
                    grown = True
    finally:
        kernel32.CloseHandle(snapshot)
    return family


def window_class(hwnd, size=256):
    buffer = ctypes.create_unicode_buffer(size)
    user32.GetClassNameW(hwnd, buffer, size)
    return buffer.value


def window_text(hwnd, size=1024):
    if not hwnd:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    user32.GetWindowTextW(hwnd, buffer, size)
    return buffer.value


def window_pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def find_windows(class_name, pid, visible_only=True):
    wanted = process_family(pid)
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, lparam):
        if window_class(hwnd) == class_name and window_pid(hwnd) in wanted:
            if not visible_only or user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found


def child_windows(parent):
    children = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumChildWindows(parent, callback_type(lambda h, l: (children.append(h), True)[1]), 0)
    return children


def control_id(hwnd):
    return user32.GetWindowLongPtrW(hwnd, GWL_ID)


def dialog_parts(dialog, ids):
    parts = {}
    label = ""
    for child in child_windows(dialog):
        key = (window_class(child).upper(), control_id(child))
        parts[key] = child
        if key[0] == "STATIC" and key[1] == ids.get("label"):
            label = window_text(child)
    edit = parts.get(("EDIT", ids.get("edit", -1)))
    listbox = parts.get(("LISTBOX", ids.get("list", -1)))
    masked = bool(user32.SendMessageW(edit, 0x00D2, 0, 0)) if edit else False   # EM_GETPASSWORDCHAR
    return {"label": label, "edit": edit, "list": listbox, "masked": masked,
            "ok": parts.get(("BUTTON", ids.get("ok", -1))),
            "show": parts.get(("BUTTON", ids.get("show", -1)))}


def main_controls(main_hwnd):
    controls = {}
    for child in child_windows(main_hwnd):
        for name, cid in MAIN_IDS.items():
            if control_id(child) == cid:
                controls[name] = child
    return controls


def focus_of(thread_id):
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return None
    return info.hwndFocus


def wait_for_window(class_name, pid, timeout=20.0, exclude=(), visible_only=True):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd in find_windows(class_name, pid, visible_only):
            if hwnd not in exclude:
                return hwnd
        time.sleep(0.15)
    return None


def wait_gone(hwnd, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not user32.IsWindow(hwnd):
            return True
        time.sleep(0.15)
    return not user32.IsWindow(hwnd)


def is_topmost(hwnd):
    """对话框必须是"总在最前"：本进程不在前台时 SetForegroundWindow 可能失败，
    那时模态框会躲在别的窗口后面，而主窗已被禁用 —— 用户看到的就是"整个程序点不动"。"""
    return bool(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def type_text(hwnd, text):
    for char in text:
        if not user32.PostMessageW(hwnd, WM_CHAR, ord(char), 0):
            return False
        time.sleep(0.02)
    time.sleep(0.15)
    return True


def press_key(hwnd, vk):
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
    time.sleep(0.3)


def stream_stopped(log_path, settle=1.5, window=2.5, tolerance=200):
    """打断之后，界面日志应该不再增长（每收到一段思维链都会写日志）。"""
    time.sleep(settle)
    before = len(log_text(log_path))
    time.sleep(window)
    return len(log_text(log_path)) - before <= tolerance


def log_text(path):
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def wait_log(path, needle, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if needle in log_text(path):
            return True
        time.sleep(0.2)
    return False


OUTPUT_LINE_HEIGHT = 20        # 与 dsh-mini.py 里的 OUTPUT_LINE_HEIGHT 一致


def output_view(edit):
    """对话区当前视野：(总行数, 首行行号, 客户区高, 可见行数, 理想首行)。

    理想首行 = 总行数 - 可见行数 —— 末行正好落在视口底部时的首行行号。
    """
    total = int(user32.SendMessageW(edit, EM_GETLINECOUNT, 0, 0))
    first = int(user32.SendMessageW(edit, EM_GETFIRSTVISIBLELINE, 0, 0))
    rect = wintypes.RECT()
    user32.GetClientRect(edit, ctypes.byref(rect))
    client_h = rect.bottom - rect.top
    visible = max(1, client_h // OUTPUT_LINE_HEIGHT)
    return total, first, client_h, visible, max(0, total - visible)


class Report(object):
    def __init__(self):
        self.items = []

    def check(self, name, ok, detail=""):
        self.items.append((name, bool(ok), detail))
        print("%s %s%s" % ("[PASS]" if ok else "[FAIL]", name,
                           ("  -> " + str(detail)) if (detail and not ok) else ""))

    def summary(self):
        failed = [name for name, ok, _ in self.items if not ok]
        print("\n合计 %d 项，通过 %d 项，失败 %d 项"
              % (len(self.items), len(self.items) - len(failed), len(failed)))
        if failed:
            print("失败项：" + "；".join(failed))
        return 0 if not failed else 1


# ============================ 测试主体 ============================

def fill_setup_steps(proc, main_hwnd, report, values, prefix="设置第 %d 步"):
    """走完"接口地址 + API Key"两步（每步都真的用键盘打字）。"""
    typed_ok = True
    for index, (want_label, want_text, is_secret) in enumerate(values, 1):
        dialog = wait_for_window("DshMiniInputDlg", proc.pid, timeout=25)
        if not dialog:
            report.check(prefix % index + "：弹出输入框", False, "没等到窗口")
            return False
        parts = dialog_parts(dialog, INPUT_IDS)
        time.sleep(0.8)                                  # 等激活消息走完再看焦点
        report.check(prefix % index + "：标签是 %r" % want_label,
                     parts["label"] == want_label, "实际 %r" % parts["label"])
        report.check(prefix % index + "：总在最前（不会被别的窗口盖住，主窗才不至于像死机）",
                     is_topmost(dialog), "缺 WS_EX_TOPMOST")
        report.check(prefix % index + "：激活后焦点仍在编辑框上（能打字）",
                     focus_of(user32.GetWindowThreadProcessId(dialog, None)) == parts["edit"],
                     "焦点=%s 编辑框=%s" % (focus_of(user32.GetWindowThreadProcessId(dialog, None)),
                                            parts["edit"]))
        report.check(prefix % index + "：主窗标题提示「请先完成弹出的对话框」",
                     "请先完成弹出的对话框" in (window_text(main_hwnd) or ""),
                     window_text(main_hwnd))
        report.check(prefix % index + "：主窗被禁用（真模态）",
                     not user32.IsWindowEnabled(main_hwnd), "")
        if is_secret:
            report.check(prefix % index + "：密钥打码 + 有“显示密钥”",
                         parts["masked"] and bool(parts["show"]), "")
        if not type_text(parts["edit"], want_text):
            typed_ok = False
        if index == 1:
            press_key(parts["edit"], VK_RETURN)          # 第 1 步走真键盘回车
        else:
            user32.PostMessageW(dialog, WM_COMMAND, INPUT_IDS["ok"], 0)
        if not wait_gone(dialog):
            user32.PostMessageW(dialog, WM_COMMAND, INPUT_IDS["ok"], 0)
            time.sleep(0.5)
        time.sleep(0.4)
    return typed_ok


def wait_idle(controls, timeout=60.0):
    """等这一轮跑完。

    用「停止」按钮的可用状态当忙闲指示：只有在跑的时候它才是可点的，
    比读日志猜要可靠，也是进程外能看到的信号。
    """
    stop = controls.get("stop")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not stop or not user32.IsWindowEnabled(stop):
            return True
        time.sleep(0.2)
    return False


def send_message(main_hwnd, controls, text):
    """把消息打进输入框并回车发送（等于用户手打）。"""
    wait_idle(controls)
    focus = focus_of(user32.GetWindowThreadProcessId(main_hwnd, None))
    target = focus if focus in (controls.get("input"),) else controls["input"]
    type_text(target, text)
    press_key(controls["input"], VK_RETURN)
    time.sleep(0.3)


def main():
    parser = argparse.ArgumentParser(description="dsh-mini GUI 端到端测试")
    parser.add_argument("--exe", help="测打包好的 dsh-mini.exe（不给就测 dev/dsh-mini.py）")
    parser.add_argument("--python", default=sys.executable or "python",
                        help="跑源码时用的解释器（默认当前解释器）")
    parser.add_argument("--keep", action="store_true", help="保留临时目录（排错用）")
    args = parser.parse_args()

    dev_dir = os.path.dirname(os.path.abspath(__file__))
    source = os.path.join(dev_dir, "dsh-mini.py")
    work = tempfile.mkdtemp(prefix="dsh-mini-e2e-")
    report = Report()
    gateway = FakeGateway(mode="reasoning")
    print("测试目录：%s" % work)
    print("假网关：%s（不联网、不碰用户接口）" % gateway.base_url)

    if args.exe:
        exe = os.path.abspath(args.exe)
        if not os.path.isfile(exe):
            print("找不到 exe：%s" % exe)
            gateway.close()
            return 2
        shutil.copy(exe, os.path.join(work, os.path.basename(exe)))
        command = [os.path.join(work, os.path.basename(exe)), "--gui"]
        print("被测对象：%s（exe）" % exe)
    else:
        if not os.path.isfile(source):
            print("找不到源码：%s" % source)
            gateway.close()
            return 2
        shutil.copy(source, os.path.join(work, "dsh-mini.py"))
        command = [args.python, "dsh-mini.py", "--gui"]
        print("被测对象：%s（源码）" % source)

    config_path = os.path.join(work, "dsh-mini.config.json")
    log_path = os.path.join(work, "dsh-mini-gui.log")
    # 首次使用场景：没有 api_key（程序应当自动弹出设置）
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump({"base_url": gateway.base_url, "api_key": "", "model": "fake-chat",
                   "model_picker": False, "save_sessions": False, "show_reasoning": True},
                  handle, ensure_ascii=False)

    proc = subprocess.Popen(command, cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        main_hwnd = wait_for_window("DshMiniGuiWnd", proc.pid, timeout=40)
        report.check("主窗口能起来", bool(main_hwnd), "没等到 DshMiniGuiWnd")
        if not main_hwnd:
            return report.summary()
        thread_id = user32.GetWindowThreadProcessId(main_hwnd, None)
        controls = main_controls(main_hwnd)

        # ---------- 1) 首次使用：自动弹出设置，两步，然后自动进模型选择 ----------
        first = wait_for_window("DshMiniInputDlg", proc.pid, timeout=25)
        report.check("首次启动（没有密钥）自动弹出设置", bool(first), "没有自动弹输入框")
        if first:
            report.check("设置只有两步（不再有单独的模型输入框）",
                         "第 1/2 步" in (dialog_parts(first, INPUT_IDS)["label"] or "")
                         or "1/2" in (window_text(first) or ""),
                         "标题/标签：%r / %r" % (window_text(first),
                                               dialog_parts(first, INPUT_IDS)["label"]))
            user32.PostMessageW(first, WM_COMMAND, INPUT_IDS["cancel"], 0)
            wait_gone(first)
            user32.PostMessageW(main_hwnd, WM_COMMAND, MENU_SETUP, 0)

        key_one = "sk-e2e-first-key"
        typed_ok = fill_setup_steps(proc, main_hwnd, report,
                                    [("接口地址 base_url", gateway.base_url, False),
                                     ("API Key（留空=保持原值）", key_one, True)])
        time.sleep(0.8)
        with open(config_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        report.check("设置写盘（界面日志出现“配置已保存”）",
                     "配置已保存" in log_text(log_path), log_text(log_path)[-300:])
        if typed_ok:
            report.check("键盘打进去的密钥进了配置", saved.get("api_key") == key_one,
                         "实际 %r" % saved.get("api_key"))
        report.check("保存后自动进入“选择模型”", bool(wait_for_window("DshMiniChoiceDlg", proc.pid, 25)),
                     "没等到 DshMiniChoiceDlg")
        choice = wait_for_window("DshMiniChoiceDlg", proc.pid, 5)
        if choice:
            parts = dialog_parts(choice, CHOICE_IDS)
            items = []
            if parts["list"]:
                count = int(user32.SendMessageW(parts["list"], 0x018B, 0, 0))       # LB_GETCOUNT
                for index in range(max(0, count)):
                    length = int(user32.SendMessageW(parts["list"], 0x018A, index, 0))
                    buffer = ctypes.create_unicode_buffer(length + 2)
                    user32.SendMessageW(parts["list"], 0x0189, index,
                                        ctypes.cast(buffer, ctypes.c_void_p).value)
                    items.append(buffer.value)
            report.check("模型列表来自接口 /models", "fake-reasoner" in items and "fake-chat" in items,
                         "列表=%s" % items)
            report.check("模型框焦点在列表上（↑↓ 直接选）",
                         focus_of(thread_id) == parts["list"], "焦点=%s" % focus_of(thread_id))
            type_text(parts["edit"], "fake-reasoner")
            press_key(parts["edit"], VK_RETURN)
            report.check("选完模型窗口关闭", wait_gone(choice), "窗口还在")
            report.check("模型切换生效", wait_log(log_path, "模型已切换为 fake-reasoner", 10),
                         log_text(log_path)[-300:])
        report.check("新密钥立刻用于请求（client 不再用缓存的旧值）",
                     any(key_one in item for item in gateway.auth),
                     "网关收到的 Authorization：%s" % gateway.auth[:3])

        # ---------- 2) 一轮对话：思考过程可见 + 视图贴底 ----------
        before = len(log_text(log_path))
        send_message(main_hwnd, controls, "hello-reasoning-round")
        report.check("发出后能看到模型思考过程（TUI 里有的那个）",
                     wait_log(log_path, "思考第1步", 20), "日志里没有思维链")
        report.check("思考过程有独立前缀（…）", "… " in log_text(log_path)[before:], "")
        report.check("正文照常显示", wait_log(log_path, "这是最终回答", 20), "")

        # 长输出 → 视图必须贴底
        gateway.mode = "long"
        send_message(main_hwnd, controls, "hello-long-output")
        report.check("长输出跑完", wait_log(log_path, "输出行 120", 30), "没等到长输出结束")
        time.sleep(0.8)
        total, first_line, client_h, visible, ideal = output_view(controls["output"])
        report.check("输出多了以后视图贴在下边缘（最新一行在视口底部）",
                     abs(first_line - ideal) <= 1,
                     "总行=%d 首行=%d 可见≈%d 理想首行=%d（差 %d 行）"
                     % (total, first_line, visible, ideal, ideal - first_line))

        # ---------- 3) F2 改密钥：立刻生效 ----------
        gateway.mode = "reasoning"
        key_two = "sk-e2e-second-key"
        user32.PostMessageW(main_hwnd, WM_COMMAND, MENU_SETUP, 0)
        fill_setup_steps(proc, main_hwnd, report,
                         [("接口地址 base_url", gateway.base_url, False),
                          ("API Key（留空=保持原值）", key_two, True)], prefix="改配置第 %d 步")
        time.sleep(0.5)
        user32.PostMessageW(main_hwnd, WM_COMMAND, INPUT_IDS["ok"], 0)   # 关掉自动弹出的模型框
        time.sleep(0.5)
        # 关掉可能出现的模型选择框（取消），再发一条消息验证用的新密钥
        for _ in range(3):
            chooser = find_windows("DshMiniChoiceDlg", proc.pid)
            if not chooser:
                break
            for dialog in chooser:
                user32.PostMessageW(dialog, WM_COMMAND, CHOICE_IDS["cancel"], 0)
            time.sleep(0.5)
        time.sleep(0.8)
        report.check("对话框都关掉后焦点回到输入框（不用先点一下就能打字）",
                     focus_of(thread_id) == controls["input"],
                     "焦点=%s 输入框=%s" % (focus_of(thread_id), controls["input"]))
        before_typing = len(log_text(log_path))
        send_message(main_hwnd, controls, "typing-right-after-dialogs")
        report.check("紧接着就能发消息（输入状态没卡住）",
                     wait_log(log_path, "typing-right-after-dialogs", 10),
                     log_text(log_path)[before_typing:][:200])
        send_message(main_hwnd, controls, "hello-after-key-change")
        report.check("改完密钥不需要重启（新密钥马上用在请求里）",
                     wait_log(log_path, "这是最终回答", 20)
                     and any(key_two in item for item in gateway.auth),
                     "网关收到的 Authorization：%s" % gateway.auth[-3:])

        # ---------- 4) Esc 打断（焦点不在输入框时也要管用） ----------
        gateway.mode = "endless"
        gateway.reasoning_sent = 0
        send_message(main_hwnd, controls, "hello-endless-thinking")
        report.check("思考流已经开始", wait_log(log_path, "还在想 1", 20), "")
        time.sleep(0.8)
        report.check("思考时状态栏提示可打断（Esc / 停止）",
                     "思考中" in (window_text(controls["status"]) or ""),
                     "状态栏=%r" % window_text(controls["status"]))
        time.sleep(0.6)
        user32.SetFocus(controls["send"])          # 故意把焦点挪到按钮上，模拟"没点过输入框"
        press_key(main_hwnd, VK_ESCAPE)            # Esc 发给主窗
        report.check("Esc 打断：界面提示已打断", wait_log(log_path, "已打断", 10), "")
        report.check("Esc 打断：服务端吐出来的内容确实停了（日志不再增长）",
                     stream_stopped(log_path), "打断后界面还在继续长")

        # ---------- 5) 「停止」按钮 ----------
        send_message(main_hwnd, controls, "hello-stop-button")
        report.check("第二轮思考流开始", wait_log(log_path, "还在想 1", 20), "")
        time.sleep(0.5)
        user32.PostMessageW(main_hwnd, WM_COMMAND, ID_STOP, 0)
        report.check("点「停止」也能打断（日志不再增长）", stream_stopped(log_path),
                     "点停止后界面还在继续长")

        # ---------- 6) 输入框焦点（打完一轮还能直接打字） ----------
        report.check("一轮结束后焦点回到输入框", focus_of(thread_id) == controls["input"],
                     "焦点=%s" % focus_of(thread_id))
    finally:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        gateway.close()
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print("临时目录保留在：%s" % work)
    return report.summary()


if __name__ == "__main__":
    sys.exit(main())
