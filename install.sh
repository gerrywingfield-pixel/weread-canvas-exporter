#!/bin/bash
# ==============================================================
# 微信读书导出工具 — 一键环境安装脚本
# 用法: ./install.sh
# ==============================================================

set -e

echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║    微信读书导出工具 — 环境安装                 ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo ""

# ─── 1. 检测 Python ──────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo "  ❌ 未检测到 Python3"
    echo "     请先安装: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

PY_VER=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
echo "  ✅ Python $PY_VER"

# ─── 2. 虚拟环境检测与创建 ──────────────────────────────
IN_VENV=false
if [ -n "$VIRTUAL_ENV" ]; then
    IN_VENV=true
fi

if [ "$IN_VENV" = false ]; then
    echo "  ⚠️  未检测到虚拟环境，正在创建..."
    if ! python3 -m venv --help &>/dev/null; then
        echo "  ❌ python3-venv 未安装，请执行: sudo apt install python3-venv"
        exit 1
    fi
    python3 -m venv venv
    echo "  ✅ 虚拟环境已创建: ./venv/"
    echo "  激活后继续安装..."
    source venv/bin/activate
    echo "  ✅ 虚拟环境已激活"
else
    echo "  ✅ 虚拟环境已激活: $VIRTUAL_ENV"
fi

# ─── 3. 安装 Python 依赖 ──────────────────────────────
echo ""
echo "  ── 安装 Python 依赖..."
pip install playwright -q 2>&1 | tail -1
echo "  ✅ playwright 安装完成"

# ─── 4. 安装 Chromium 浏览器 ───────────────────────────
echo ""
echo "  ── 安装 Chromium 浏览器（约 200MB，首次安装较慢）..."
python -m playwright install chromium 2>&1 | tail -3
echo "  ✅ Chromium 安装完成"

# ─── 5. 验证安装 ──────────────────────────────────────
echo ""
echo "  ── 验证安装..."
python -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.launch(headless=True)
b.close()
p.stop()
print('  ✅ Playwright + Chromium 可用')
"
echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║  安装完成！                                   ║"
echo "  ║                                               ║"
echo "  ║  运行: python weread_exporter.py               ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo ""