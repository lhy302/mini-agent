# -*- coding: utf-8 -*-
"""真实接口端到端测试（用你自己的网关/密钥，跑 GUI 全流程）。

和 gui-e2e.py 的分工：
  * gui-e2e.py   假网关，验证"界面行为"（焦点、模态、滚动、打断…），不联网；
  * 本脚本       真接口，验证"模型真的能聊":思维链能不能看见、正文能不能显示、
                 打断能不能真的停住服务端还在吐的内容。

用法（在 dev 目录下）：

    python real-api-test.py                                  # 自动找配置文件
    python real-api-test.py --config D:\\path\\dsh-mini.config.json
    python real-api-test.py --exe ..\\dsh-mini.exe             # 测打包产物
    python real-api-test.py --keep                           # 保留临时目录

密钥只会被读进内存（并在临时目录里写一份副本），**不会打印**。
被测程序在临时目录里跑，不碰你原来的配置、日志和会话。
"""
import argparse
import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# 复用 gui-e2e.py 里的 Win32 工具（文件名带连字符，用 importlib 加载）
_spec = importlib.util.spec_from_file_location("gui_e2e", os.path.join(HERE, "gui-e2e.py"))
gui_e2e = importlib.util.module_from_spec(_spec)
sys.modules["gui_e2e"] = gui_e2e
_spec.loader.exec_module(gui_e2e)

user32 = gui_e2e.user32
wintypes = gui_e2e.wintypes

CANDIDATE_CONFIGS = (
    os.path.join(os.getcwd(), "dsh-mini.config.json"),
    os.path.join(HERE, "dsh-mini.config.json"),
    os.path.join(os.path.dirname(HERE), "dsh-mini.config.json"),
)

REASON_PROMPT = "请一步一步推导 1234 × 5678 等于多少，过程写详细一点。"
PLAIN_PROMPT = "只回一句话：你好。"


def find_config(explicit):
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    for path in CANDIDATE_CONFIGS:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as handle:
                    data = json.load(handle)
            except Exception:
                continue
            if isinstance(data, dict) and data.get("api_key"):
                return path
    return None


def fetch_models(base_url, api_key, timeout=15):
    import urllib.request
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    request = urllib.request.Request(url + "/models",
                                     headers={"Authorization": "Bearer %s" % api_key})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    ids = [str(item["id"]) for item in (data.get("data") or []) if isinstance(item, dict) and item.get("id")]
    return sorted(set(ids))


def main():
    parser = argparse.ArgumentParser(description="dsh-mini 真实接口端到端测试")
    parser.add_argument("--config", help="借用哪个配置文件（默认自动找带 api_key 的）")
    parser.add_argument("--exe", help="测打包好的 dsh-mini.exe（不给就测 dev/dsh-mini.py）")
    parser.add_argument("--python", default=sys.executable or "python")
    parser.add_argument("--timeout", type=float, default=90.0, help="等模型回复的秒数")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    config_path = find_config(args.config)
    if not config_path:
        print("没找到带 api_key 的配置文件，用 --config 指定一个。")
        return 2
    with open(config_path, "r", encoding="utf-8-sig") as handle:
        borrowed = json.load(handle)
    api_key = borrowed.get("api_key") or ""
    base_url = borrowed.get("base_url") or ""
    print("借用配置：%s" % config_path)
    print("接口地址：%s" % base_url)
    print("密钥：已读到（长度 %d，不打印）" % len(api_key))

    models = []
    try:
        models = fetch_models(base_url, api_key)
        print("接口可用模型：%s" % ("、".join(models) or "（空）"))
    except Exception as exc:
        print("! 取模型列表失败：%s" % exc)
        print("  （下面照样试，可能模型名是手填的）")
    model = borrowed.get("model") or ""
    if models and model not in models:
        print("! 配置里的模型 %r 不在接口列表里，改用 %r" % (model, models[0]))
        model = models[0]
    if not model:
        print("配置里没有模型名，且接口没返回列表，无法继续。")
        return 2
    print("本次使用模型：%s" % model)
    print("-" * 60)

    work = tempfile.mkdtemp(prefix="dsh-mini-real-")
    report = gui_e2e.Report()
    source = os.path.join(HERE, "dsh-mini.py")
    if args.exe:
        exe = os.path.abspath(args.exe)
        if not os.path.isfile(exe):
            print("找不到 exe：%s" % exe)
            return 2
        shutil.copy(exe, os.path.join(work, os.path.basename(exe)))
        command = [os.path.join(work, os.path.basename(exe)), "--gui"]
        print("被测对象：%s（exe）" % exe)
    else:
        shutil.copy(source, os.path.join(work, "dsh-mini.py"))
        command = [args.python, "dsh-mini.py", "--gui"]
        print("被测对象：%s（源码）" % source)

    # 临时配置：借用真实地址/密钥，其余按测试需要
    local_cfg = dict(borrowed)
    local_cfg.update({"base_url": base_url, "api_key": api_key, "model": model,
                      "model_picker": False, "save_sessions": False, "show_reasoning": True,
                      "stream": True, "cwd": work})
    cfg_path = os.path.join(work, "dsh-mini.config.json")
    with open(cfg_path, "w", encoding="utf-8") as handle:
        json.dump(local_cfg, handle, ensure_ascii=False, indent=2)
    log_path = os.path.join(work, "dsh-mini-gui.log")

    proc = subprocess.Popen(command, cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        main_hwnd = gui_e2e.wait_for_window("DshMiniGuiWnd", proc.pid, timeout=40)
        report.check("主窗口能起来", bool(main_hwnd), "")
        if not main_hwnd:
            return report.summary()
        controls = gui_e2e.main_controls(main_hwnd)

        # ---- 1) 普通一问一答：正文 + 思维链都要看得见 ----
        gui_e2e.send_message(main_hwnd, controls, REASON_PROMPT)
        report.check("真接口有回复（正文开始流式输出）",
                     gui_e2e.wait_log(log_path, "● ", args.timeout),
                     "日志尾部：%s" % gui_e2e.log_text(log_path)[-200:])
        text = gui_e2e.log_text(log_path)
        report.check("正文显示出来（● 开头）", "● " in text, "")
        report.check("思维链显示出来（… 开头）——TUI 有、GUI 也必须有的那个",
                     "… " in text, "日志里没有“…”段落，说明模型没吐思维链或被关掉了")
        if "… " in text:
            head = text.split("… ", 1)[1].split("\n", 1)[0]
            print("   思维链开头：%s" % head[:80])
        # 这个提示词本身就要算很久，等不到空闲不算失败：按 Esc 收尾后继续
        if not gui_e2e.wait_idle(controls, timeout=args.timeout):
            print("   （%ds 内这一轮还没跑完，按 Esc 收尾后继续测后面的项）" % args.timeout)
            gui_e2e.press_key(main_hwnd, gui_e2e.VK_ESCAPE)
            gui_e2e.wait_idle(controls, timeout=20)

        # ---- 2) 打断：思考过程中按 Esc（真接口还在吐 token）----
        before = len(gui_e2e.log_text(log_path))
        gui_e2e.send_message(main_hwnd, controls, REASON_PROMPT + "再算一遍 98765 × 4321。")
        time.sleep(3.0)                      # 等它开始吐
        user32.SetFocus(controls["send"])    # 故意把焦点挪走，模拟"没点过输入框"
        gui_e2e.press_key(main_hwnd, gui_e2e.VK_ESCAPE)
        report.check("Esc 打断：提示已打断", gui_e2e.wait_log(log_path, "已打断", 10), "")
        snapshot = gui_e2e.log_text(log_path)
        time.sleep(4.0)
        report.check("Esc 打断：确实停住了（之后不再增长）",
                     len(gui_e2e.log_text(log_path)) - len(snapshot) < 400,
                     "打断后日志又长了 %d 字节" % (len(gui_e2e.log_text(log_path)) - len(snapshot)))

        # ---- 3) 输出滚动：出长内容后视图要贴在下边缘 ----
        time.sleep(0.5)
        total, first_line, client_h, visible, ideal = gui_e2e.output_view(controls["output"])
        report.check("输出多了以后视图贴在下边缘（最新一行在视口底部）",
                     abs(first_line - ideal) <= 1,
                     "总行=%d 首行=%d 可见≈%d 理想首行=%d" % (total, first_line, visible, ideal))
        report.check("一轮结束后焦点回到输入框",
                     gui_e2e.focus_of(user32.GetWindowThreadProcessId(main_hwnd, None))
                     == controls["input"], "")
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
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print("临时目录保留在：%s" % work)
    return report.summary()


if __name__ == "__main__":
    sys.exit(main())
