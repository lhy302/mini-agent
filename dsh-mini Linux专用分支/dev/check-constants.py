# -*- coding: utf-8 -*-
"""静态检查：self.XXX 这种常量/属性，在本类里到底有没有定义（AttributeError 隐患）。

起因：v1.1.2 起 DshGui._handle_key 一直在用 self.VK_ESCAPE，但 DshGui 类里
从来没定义过它 —— 于是**每一次按键**都在第一行抛 AttributeError，
被 ctypes 的窗口过程回调静默吞掉（无控制台打包时 stderr 也没人看），
结果"回车发送 / Esc 打断 / F1..F8 / Ctrl 系列"全部形同虚设。

用法：python check-constants.py [源码路径]
退出码 0 = 没发现问题。
"""
import os
import re
import sys


def analyze(path):
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    globals_defined = set()
    for line in lines:
        match = re.match(r"^([A-Z][A-Z0-9_]*(?:\s*,\s*[A-Z][A-Z0-9_]*)*)\s*(?::[^=]*)?=(?!=)", line)
        if match:
            for name in match.group(1).split(","):
                globals_defined.add(name.strip())

    # 切成"类块"：从 ^class 到下一个顶层语句
    blocks = []          # (class_name, start, end)
    current = None
    for index, line in enumerate(lines):
        if re.match(r"^class\s+(\w+)", line):
            if current:
                blocks.append((current[0], current[1], index - 1))
            name = re.match(r"^class\s+(\w+)", line).group(1)
            current = (name, index)
        elif current and line.strip() and not line[0].isspace():
            blocks.append((current[0], current[1], index - 1))
            current = None
    if current:
        blocks.append((current[0], current[1], len(lines) - 1))

    problems = []
    for name, start, end in blocks:
        body = "\n".join(lines[start:end + 1])
        own = set()
        for line in lines[start:end + 1]:
            match = re.match(r"^\s+([A-Z][A-Z0-9_]*(?:\s*,\s*[A-Z][A-Z0-9_]*)*)\s*(?::[^=]*)?=(?!=)", line)
            if match:
                for item in match.group(1).split(","):
                    own.add(item.strip())
        own |= set(re.findall(r"self\.([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)", body))
        for offset, line in enumerate(lines[start:end + 1]):
            for match in re.finditer(r"self\.([A-Z][A-Z0-9_]{2,})\b", line):
                found = match.group(1)
                if found not in own and found not in globals_defined:
                    problems.append((start + offset + 1, name, found))
    return blocks, problems


def main():
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dsh-mini.py")
    path = sys.argv[1] if len(sys.argv) > 1 else default_path
    blocks, problems = analyze(path)
    print("文件：%s" % path)
    print("类：%d 个" % len(blocks))
    for line_no, class_name, name in problems:
        print("  !! 第 %d 行 %s.self.%s —— 本类里没有定义（AttributeError 隐患）"
              % (line_no, class_name, name))
    if not problems:
        print("  （没发现问题）")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
