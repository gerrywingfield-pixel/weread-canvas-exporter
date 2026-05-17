#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
认证模块 - 简化版
仅加载和保存cookie，不包含浏览器自动化
"""

import os
from pathlib import Path

def load_cookie():
    """从cookie.txt加载cookie"""
    cookie_path = Path(__file__).parent / "cookie.txt"
    
    if cookie_path.exists():
        with open(cookie_path, 'r', encoding='utf-8') as f:
            cookie = f.read().strip()
        return cookie if cookie else None
    return None

def save_cookie(cookie):
    """保存cookie到cookie.txt"""
    cookie_path = Path(__file__).parent / "cookie.txt"
    with open(cookie_path, 'w', encoding='utf-8') as f:
        f.write(cookie)
    return True

def check_cookie_validity(cookie):
    """检查cookie是否有效（简单检查）"""
    if not cookie:
        return False
    
    # 如果cookie包含常见的登录相关关键词，认为是有效的
    login_keywords = ['wr_avatar', 'wr_name', 'wxuin', 'openid']
    if any(keyword in cookie for keyword in login_keywords):
        return True
    
    return False

if __name__ == "__main__":
    cookie = load_cookie()
    if cookie:
        print(f"Loaded cookie: {cookie[:100]}...")
        print(f"Cookie length: {len(cookie)}")
        print(f"Cookie valid: {check_cookie_validity(cookie)}")
    else:
        print("No cookie found")
