#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
项目配置
"""

import os

# 基于脚本所在目录自动计算路径（不依赖固定用户名）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE_PATH = os.path.join(BASE_DIR, "cookie.txt")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# 抓取配置
MAX_PAGES_PER_BOOK = 5000
Y_TOLERANCE = 2  # 文字重组Y坐标容差
HEADLESS_MODE = True  # 无头模式
CHROME_BIN = None  # 自动检测

# 书籍信息
DEFAULT_BOOK_ID = ""  # 运行时从书架选择