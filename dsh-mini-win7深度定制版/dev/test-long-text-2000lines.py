# -*- coding: utf-8 -*-
"""2000 行超长文本间断输出测试（分 20 轮对话输出，每行严格 100 字，行号连续追加）。

测试规格：
- 轮数：20 轮对话
- 每轮行数：100 行
- 总行数：2000 行（行号从 Line 0001 连续追加到 Line 2000）
- 每行长度：严格 100 字（字符）
- 总字符量：200,000+ 字（20 万字以上，彻底压测并击穿原生 32K 限制）
- 最后一轮输出后：调用原生屏幕截图保存为图片，用于视觉校验最后一行（Line 2000）是否正确清晰显示且末尾贴底。
"""
import ctypes
from ctypes import wintypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
spec = importlib.util.spec_from_file_location("dsh_mini", os.path.join(os.path.dirname(__file__), "dsh-mini.py"))
dsh_mini = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dsh_mini)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

def make_100_char_line(line_no):
    """构造严格 100 个字符的单行文本，带顺序行号前缀。"""
    prefix = "Line %04d: " % line_no
    pattern = "甲乙丙丁戊己庚辛壬癸天地玄黄宇宙洪荒日月盈昃辰宿列张金木水火土春夏秋冬"
    needed = 100 - len(prefix)
    fill = (pattern * ((needed // len(pattern)) + 1))[:needed]
    line = prefix + fill
    assert len(line) == 100, "Line length must be 100, got %d" % len(line)
    return line

def main():
    print("======================================================================")
    print(" 开始 2000 行超长文本间断输出测试（分 20 轮对话，每轮 100 行，每行 100 字）")
    print("======================================================================")

    config = dict(dsh_mini.DEFAULT_CONFIG)
    config["api_key"] = ""
    config["model_picker"] = False
    config["save_sessions"] = False

    gui = dsh_mini.DshGui(config, autoclose=0)

    test_results = []
    def check(title, cond, detail=""):
        test_results.append((title, bool(cond), detail))
        print("%s %s%s" % ("[PASS]" if cond else "[FAIL]", title, (" -> " + str(detail)) if detail and not cond else ""))

    screenshot_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_2000lines_final_render.png")

    def run_dialogue_turns():
        # 等待窗口创建完毕
        for _ in range(50):
            if gui.hwnd and user32.IsWindow(gui.hwnd) and gui.controls.get("output"):
                break
            time.sleep(0.05)

        time.sleep(0.4)
        edit_hwnd = gui.controls["output"]

        try:
            total_rounds = 20
            lines_per_round = 100
            current_line_no = 1

            for round_idx in range(1, total_rounds + 1):
                start_line = current_line_no
                end_line = current_line_no + lines_per_round - 1

                # 模拟用户提问与助手响应前导标记
                round_header = "\n› 用户: 请输出第 %02d 轮文本（行号 %04d ~ %04d）\n● 助手:\n" % (
                    round_idx, start_line, end_line
                )
                gui.push(round_header)

                # 组装本轮 100 行，每行严格 100 字符
                lines = []
                for lno in range(start_line, end_line + 1):
                    lines.append(make_100_char_line(lno))
                current_line_no = end_line + 1

                round_text = "\n".join(lines) + "\n"
                # 推送本轮 100 行（约 10,100 字符）
                gui.push(round_text)

                # 间断等待，模拟真实多轮对话流式追加
                time.sleep(0.12)

                # 报告每 5 轮的进度
                if round_idx % 5 == 0 or round_idx == total_rounds:
                    cur_chars = user32.GetWindowTextLengthW(edit_hwnd)
                    cur_lines = user32.SendMessageW(edit_hwnd, gui.EM_GETLINECOUNT, 0, 0)
                    print("  [进度] 第 %02d/20 轮已完成输出，当前控件字符数: %d，当前行数: %d" % (
                        round_idx, cur_chars, cur_lines
                    ))

            # 20 轮全部输出完毕后，充分等待 GUI 渲染完成
            time.sleep(1.0)

            # -------------------------------------------------------------
            # 断言 1：总行数达到 2000 行以上
            # -------------------------------------------------------------
            final_lines = int(user32.SendMessageW(edit_hwnd, gui.EM_GETLINECOUNT, 0, 0))
            check("总行数达到 2000 行以上", final_lines >= 2000, "当前实际行数: %d" % final_lines)

            # -------------------------------------------------------------
            # 断言 2：总字符数达到 200,000 字符（20 万字）以上
            # -------------------------------------------------------------
            final_chars = int(user32.GetWindowTextLengthW(edit_hwnd))
            check("总字符数达到 200,000+（20 万字，彻底突破 32K 限制）",
                  final_chars >= 200000, "当前实际字符数: %d" % final_chars)

            # -------------------------------------------------------------
            # 断言 3：最新追加的最后一行 Line 2000 完整显示在控件文本最末尾
            # -------------------------------------------------------------
            last_line_marker = make_100_char_line(2000)
            check("最后一行第 2000 行 (Line 2000) 成功追加且未丢失",
                  last_line_marker in gui.text,
                  "gui.text 末尾 200 字符: " + repr(gui.text[-200:]))

            # -------------------------------------------------------------
            # 断言 4：视图滚动精准对齐在最底部（最后那轮紧贴视口底部）
            # -------------------------------------------------------------
            first_visible = int(user32.SendMessageW(edit_hwnd, gui.EM_GETFIRSTVISIBLELINE, 0, 0))
            rect = wintypes.RECT()
            user32.GetClientRect(edit_hwnd, ctypes.byref(rect))
            client_h = rect.bottom - rect.top
            visible_lines = max(1, client_h // dsh_mini.OUTPUT_LINE_HEIGHT)
            ideal_first = max(0, final_lines - visible_lines)

            check("视图精准贴底（首可见行与理论贴底行相符）",
                  abs(first_visible - ideal_first) <= 2,
                  "总行=%d, 首可见行=%d, 理论贴底行=%d, 相差=%d 行"
                  % (final_lines, first_visible, ideal_first, abs(first_visible - ideal_first)))

            # -------------------------------------------------------------
            # 步骤 5：调用原生 GDI+ 屏幕截图，保存现场渲染画面
            # -------------------------------------------------------------
            try:
                user32.SetForegroundWindow(gui.hwnd)
                time.sleep(0.3)
                dsh_mini.capture_screenshot(screenshot_file)
                check("最后那轮屏幕截图成功生成",
                      os.path.isfile(screenshot_file) and os.path.getsize(screenshot_file) > 10000,
                      screenshot_file)
            except Exception as exc:
                check("最后那轮屏幕截图成功生成", False, str(exc))

        finally:
            time.sleep(1.0)
            user32.PostMessageW(gui.hwnd, gui.WM_CLOSE, 0, 0)

    worker_thread = threading.Thread(target=run_dialogue_turns)
    worker_thread.daemon = True
    worker_thread.start()

    # 启动 GUI 主线程消息循环
    gui.run()

    print("======================================================================")
    passed_cnt = sum(1 for _, ok, _ in test_results if ok)
    print("测试结果：共 %d 项，通过 %d 项，失败 %d 项" % (len(test_results), passed_cnt, len(test_results) - passed_cnt))
    print("截图文件：%s" % screenshot_file)
    print("======================================================================")
    return 0 if (passed_cnt == len(test_results)) else 1

if __name__ == "__main__":
    sys.exit(main())
