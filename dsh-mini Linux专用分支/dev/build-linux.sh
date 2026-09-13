#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== [1/4] 检查 Python 环境 ==="
python3 --version

echo "=== [2/4] 检查 PyInstaller ==="
pyinstaller --version

echo "=== [3/4] 打包 Linux 单文件程序 dsh-mini ==="
pyinstaller --noconfirm --clean \
    --distpath ".." \
    --workpath "build" \
    "dsh-mini-linux.spec"

chmod +x ../dsh-mini

echo "=== [4/4] 验证单文件构建产物 ==="
ls -lh ../dsh-mini
xvfb-run -a ../dsh-mini --selftest

echo "=========================================="
echo "Linux 单文件 Agent 打包成功：../dsh-mini"
echo "=========================================="
