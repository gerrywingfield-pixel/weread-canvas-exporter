#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信读书导出工具 - Python API
提供 WeReadExporter 类，供 AI Agent 或其他 Python 程序调用。

用法:
    from weread_core import WeReadExporter

    exporter = WeReadExporter()
    exporter.login()
    books = exporter.list_books()
    for b in books:
        print(f"  [{b['id']}] {b['title']}")

    filepath = exporter.export_book(books[0]['id'])
    print(f"导出完成: {filepath}")
    exporter.close()
"""

import sys, time, os, re, json
from collections import defaultdict
from config.official_api import get_book_info, get_chapter_list, get_shelf_full, search

from playwright.sync_api import sync_playwright

# ========== 配置 ==========
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookie.txt')
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
X_THRESHOLD = 600
CONSECUTIVE_EMPTY_LIMIT = 3
PAGE_SLEEP = 2

# ========== Canvas Hook ==========
CANVAS_HOOK_JS = """
() => {
    const origFill = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function() {
        var canvas = this.canvas;
        var rect = canvas ? canvas.getBoundingClientRect() : null;
        if (!rect || rect.width === 0) return origFill.apply(this, arguments);
        if (!window._capturedTexts) window._capturedTexts = [];
        window._capturedTexts.push({
            text: arguments[0],
            x: Math.round(arguments[1]) + Math.round(rect.left),
            y: Math.round(arguments[2])
        });
        return origFill.apply(this, arguments);
    };
}
"""


def _reassemble_page(texts, side=None):
    """按坐标重组页面文本，含去重
    side=None: 左右两侧（默认）
    side='left': 仅左侧
    side='right': 仅右侧
    """
    if not texts:
        return []
    lines_dict = defaultdict(list)
    for t in texts:
        matched = False
        for existing_y in list(lines_dict.keys()):
            if abs(existing_y - t['y']) <= 0:
                # 去重：同一 y 坐标下，相同文本在相近 x 位置只保留一份
                dupe = False
                for et in lines_dict[existing_y]:
                    if et['text'] == t['text'] and abs(et['x'] - t['x']) <= 5:
                        dupe = True
                        break
                if not dupe:
                    lines_dict[existing_y].append(t)
                matched = True
                break
        if not matched:
            lines_dict[t['y']] = [t]
    left_lines = []
    right_lines = []
    for y in sorted(lines_dict.keys()):
        items = sorted(lines_dict[y], key=lambda t: t['x'])
        left_items = [t for t in items if t['x'] < X_THRESHOLD]
        right_items = [t for t in items if t['x'] >= X_THRESHOLD]
        left_text = ''.join(t['text'] for t in left_items).strip()
        right_text = ''.join(t['text'] for t in right_items).strip()
        if left_text:
            left_lines.append(left_text)
        if right_text:
            right_lines.append(right_text)
    if side == 'left':
        return left_lines
    if side == 'right':
        return right_lines
    # side=None: 两侧完整输出
    result = list(left_lines)
    if left_lines and right_lines:
        result.append('')
    result.extend(right_lines)
    return result


def _determine_start_side(raw, ch_title):
    """判断从哪一侧开始写入（芯片浪潮页面判定）
    返回 'left', 'right', 或 'all'
    
    规则：
      1. 标题文字在左侧 → 左页是标题页 → 从 left 开始
      2. 标题文字在右侧 → 右页是标题页 → 从 right 开始
      3. 仅左侧为空（0 items）→ 左页是图片标题 → 从 left 开始
      4. 仅右侧为空（0 items）→ 右页是图片标题 → 从 right 开始
      5. 两侧都空 → 从左开始
      6. 两侧都有内容且无标题文本 → 写全部
    """
    if not raw:
        return 'left'
    left_items = [t for t in raw if t['x'] < X_THRESHOLD]
    right_items = [t for t in raw if t['x'] >= X_THRESHOLD]
    norm_title = ch_title.replace('\u3000', ' ').strip()
    left_text = ' '.join(t['text'] for t in left_items).replace('\u3000', ' ')
    right_text = ' '.join(t['text'] for t in right_items).replace('\u3000', ' ')
    # 进一步归一化：Canvas 可能每个字独立渲染，去掉所有空格再比
    norm_left = left_text.replace(' ', '')
    norm_right = right_text.replace(' ', '')
    norm_title_stripped = norm_title.replace(' ', '')
    # 规则1-2：标题文字定位
    if norm_left and norm_title_stripped in norm_left:
        return 'left'
    if norm_right and norm_title_stripped in norm_right:
        return 'right'
    # 规则3-4：单侧为空（图片标题）
    if not left_items and right_items:
        return 'left'
    if not right_items and left_items:
        return 'right'
    # 规则5：两侧空
    if not left_items and not right_items:
        return 'left'
    # 规则6：两侧都有内容
    return 'all'


def _texts_identical(a, b):
    if len(a) != len(b):
        return False
    for t1, t2 in zip(a, b):
        if t1['text'] != t2['text'] or abs(t1['x'] - t2['x']) > 5:
            return False
    return True


def _normalize_text(text):
    """去除所有 Unicode 空白字符和零宽字符"""
    import re
    return re.sub(r'[\s\u200b\u200c\u200d\ufeff\u00a0]', '', str(text))


def _canvas_matches_title(raw, title):
    """检查 Canvas 文字（两侧）是否完全包含目标标题（归一化后）"""
    if not raw or not title:
        return False
    canvas_text = ''.join(t['text'] for t in raw)
    norm_canvas = _normalize_text(canvas_text)
    norm_title = _normalize_text(title)
    if not norm_canvas or not norm_title:
        return False
    return norm_title in norm_canvas


def _get_chapter_title(page):
    return page.evaluate("""() => {
        var el = document.querySelector('.renderTargetPageInfo_header_chapterTitle');
        return el ? el.textContent.trim() : '';
    }""")


# ========== TOC 分类算法 ==========

KNOWN_FIRST_LEVEL = {
    '扉页', '版权信息', '自序', '后记', '参考文献', '附录', '致谢',
    '引言', '前言', '推荐序', '序', '导论', '关于作者', '作者简介',
}


def _scan_toc_patterns(toc_texts):
    """扫描TOC，识别存在的编号模式"""
    import re
    patterns = {
        '篇部': any(re.match(r'^第[一二三四五六七八九十百千\d]+[篇部]', t) for t in toc_texts),
        '章':   any(re.match(r'^第[一二三四五六七八九十百千\d]+[章]', t) for t in toc_texts),
        '节':   any(re.match(r'^第[一二三四五六七八九十百千\d]+[节]', t) for t in toc_texts),
        '部分': any(re.match(r'^第[一二三四五六七八九十百千\d]+[部分]', t) or
                    re.match(r'^[第]?[一二三四五六七八九十百千\d]+[部分：:]', t) for t in toc_texts),
        '中文数字': any(re.match(r'^[一二三四五六七八九十]+[、．.]', t) for t in toc_texts),
    }
    return patterns


def _determine_hierarchy_levels(patterns):
    """根据模式共存情况确定层级"""
    hierarchy = {'一级': [], '二级': [], '三级': [], '四级': []}
    if patterns['篇部'] and patterns['章'] and patterns['节']:
        hierarchy['一级'].append('篇部')
        hierarchy['二级'].append('章')
        hierarchy['三级'].append('节')
    elif patterns['篇部'] and patterns['节'] and not patterns['章']:
        hierarchy['一级'].append('篇部')
        hierarchy['二级'].append('节')
    elif patterns['章'] and patterns['节']:
        hierarchy['一级'].append('章')
        hierarchy['二级'].append('节')
    elif patterns['部分'] and patterns['节']:
        hierarchy['一级'].append('部分')
        hierarchy['二级'].append('节')
    elif patterns['章'] and not patterns['节']:
        hierarchy['一级'].append('章')
    elif patterns['节'] and not patterns['章']:
        hierarchy['一级'].append('节')
    elif patterns['部分'] and not patterns['节']:
        hierarchy['一级'].append('部分')
    # 中文数字默认二级或三级
    if patterns['中文数字']:
        if '节' in hierarchy.get('三级', []):
            hierarchy['四级'].append('中文数字')
        elif '节' in hierarchy.get('二级', []):
            hierarchy['三级'].append('中文数字')
        else:
            hierarchy['二级'].append('中文数字')
    return hierarchy


def _classify_toc_entry(text, hierarchy):
    """对单个TOC条目进行层级分类"""
    import re
    if text in KNOWN_FIRST_LEVEL:
        return '一级'
    if re.match(r'^推荐序\d*', text):
        return '一级'
    if re.match(r'^第[一二三四五六七八九十百千\d]+[篇部]', text):
        return '一级' if '篇部' in hierarchy['一级'] else '二级'
    if re.match(r'^第[一二三四五六七八九十百千\d]+[章]', text):
        return '一级' if '章' in hierarchy['一级'] else '二级'
    if re.match(r'^第[一二三四五六七八九十百千\d]+[节]', text):
        for level_name, pats in hierarchy.items():
            if '节' in pats:
                return level_name
        return '三级'
    if re.match(r'^[第]?[一二三四五六七八九十百千\d]+[部分：:]', text):
        return '一级' if '部分' in hierarchy['一级'] else '二级'
    if re.match(r'^\d+ ', text):
        return '一级'
    if re.match(r'^[一二三四五六七八九十]+[、．.]', text):
        for level_name, pats in hierarchy.items():
            if '中文数字' in pats:
                return level_name
        return '三级'
    return '一级'


def _classify_toc(toc_texts):
    """完整 TOC 分类流程"""
    import re
    patterns = _scan_toc_patterns(toc_texts)
    hierarchy = _determine_hierarchy_levels(patterns)
    classified = []
    for i, text in enumerate(toc_texts):
        level = _classify_toc_entry(text, hierarchy)
        # 提取编号前缀用于唯一性检查
        prefix = ''
        m = re.match(r'^第[一二三四五六七八九十百千\d]+[篇部章节]', text)
        if m:
            prefix = m.group(0)
        elif re.match(r'^[一二三四五六七八九十]+[、．.]', text):
            m = re.match(r'^[一二三四五六七八九十]+', text)
            if m:
                prefix = m.group(0)
        classified.append({'index': i, 'text': text, 'level': level, 'prefix': prefix})
    # 唯一性检查：同一前缀出现多次 → 降级
    from collections import Counter
    prefix_counts = Counter(c['prefix'] for c in classified if c['prefix'])
    for c in classified:
        if c['prefix'] and prefix_counts[c['prefix']] > 1 and c['level'] == '一级':
            c['level'] = '二级'
    return classified


def _render_guide_tree(classified):
    """渲染导引树：一级编号，子级用树线"""
    lines = []
    first_level_num = 0
    for item in classified:
        text = item['text']
        level = item['level']
        if level == '一级':
            first_level_num += 1
            lines.append(f"\n{first_level_num} ── {text}")
        elif level == '二级':
            lines.append(f"    ├── {text}")
        elif level == '三级':
            lines.append(f"    │   ├── {text}")
        elif level == '四级':
            lines.append(f"    │   │   ├── {text}")
    return '\n'.join(lines), first_level_num


def _extract_toc(page):
    """从目录面板提取 TOC 条目文本列表"""
    # 打开目录
    page.mouse.click(10, 10)
    time.sleep(0.5)
    page.click('.readerControls_item.catalog', timeout=5000)
    time.sleep(2)
    toc = page.evaluate("""() => {
        var items = document.querySelectorAll('.readerCatalog_list_item');
        return Array.from(items).map(function(item, i) {
            var el = item.querySelector('.readerCatalog_list_item_title_text');
            return el ? el.textContent.trim() : item.textContent.trim();
        });
    }""")
    # 关闭目录（点击空白区域）
    page.mouse.click(10, 10)
    time.sleep(0.5)
    return toc


def _navigate_to_chapter(page, ch_start, toc):
    """导航到章节起始页（TOC-prev → ArrowRight 方式）"""
    prev_idx = max(0, ch_start - 1)
    prev_title = toc[prev_idx]['text'] if prev_idx < len(toc) else ''
    target_title = toc[ch_start]['text']

    # 打开目录
    page.mouse.click(10, 10)
    time.sleep(0.5)
    page.click('.readerControls_item.catalog', timeout=5000)
    time.sleep(2)

    # 点击前一个 TOC 条目
    page.locator('.readerCatalog_list_item').nth(prev_idx).click(timeout=5000)
    time.sleep(3)
    page.mouse.click(10, 10)
    time.sleep(1)

    # ArrowRight 前进到目标章节
    for step in range(30):
        current = _get_chapter_title(page)
        if current and (target_title in current or current in target_title):
            break
        # 清除前一个页面的缓存
        page.evaluate('window._capturedTexts = []')
        page.keyboard.press('ArrowRight')
        time.sleep(1.5)

    # 关闭目录
# ========== TOC 对齐：API ↔ DOM ==========


def _align_api_dom_toc(api_chapters, dom_classified):
    """对齐 API TOC level-1 章节与 DOM TOC 索引

    对每个 API level=1 章节，找到对应的 DOM TOC 索引，
    并提取该章节下所有 API level=2 的子章节标题（用于入口检测）。

    返回:
        aligned: [{api_title, dom_index, sub_titles, chapterIdx}, ...]
        按 DOM L1 顺序排列，与导引树编号对齐；无法匹配的 DOM 条目为 None
    """
    if not api_chapters or not dom_classified:
        return []

    # 获取 API 中 level=1 的章节
    api_l1 = [c for c in api_chapters if c.get('level') == 1]

    # 构建 API 标题 → 信息的映射
    api_info_map = {}
    for i, ac in enumerate(api_l1):
        sub_titles = []
        next_idx = api_l1[i + 1]['chapterIdx'] if i + 1 < len(api_l1) else 999999
        for sc in api_chapters:
            if sc.get('level') == 2 and sc['chapterIdx'] > ac['chapterIdx'] and sc['chapterIdx'] < next_idx:
                sub_titles.append(sc['title'].strip())
        api_info_map[ac['title'].strip()] = {
            'api_title': ac['title'].strip(),
            'sub_titles': sub_titles,
            'chapterIdx': ac.get('chapterIdx'),
        }

    # 获取 DOM 中 level=1 的索引列表
    dom_l1_indices = [c['index'] for c in dom_classified if c['level'] == '一级']

    # 按 DOM L1 顺序建立对齐列表
    aligned = []
    for dom_idx in dom_l1_indices:
        dom_title = dom_classified[dom_idx]['text'].strip()
        matched = None
        # 精确匹配
        if dom_title in api_info_map:
            matched = api_info_map[dom_title]
        else:
            # 子串匹配
            for at, info in api_info_map.items():
                if dom_title and (dom_title in at or at in dom_title):
                    matched = info
                    break
        if matched:
            aligned.append({**matched, 'dom_index': dom_idx})
        else:
            # 无法对齐的 DOM 条目（如扉页 vs 封面变体）
            aligned.append(None)

    return aligned


# ========== WeReadExporter 类 ==========

class WeReadExporter:
    """微信读书导出器 - 非交互式 API"""

    def __init__(self, headless=True, verbose=False):
        self.headless = headless
        self.verbose = verbose
        self._p = None
        self._browser = None
        self._context = None
        self._page = None

    def _log(self, msg):
        if self.verbose:
            t = time.strftime('%H:%M:%S')
            print(f'[{t}] {msg}', flush=True)

    # ─── Cookie 管理 ──────────────────────────────

    def _load_cookie(self):
        if not os.path.exists(COOKIE_FILE):
            return None
        with open(COOKIE_FILE) as f:
            return f.read().strip()

    def _save_cookie(self, cookie_str):
        with open(COOKIE_FILE, 'w') as f:
            f.write(cookie_str)
        self._log(f'Cookie已保存 ({len(cookie_str)} 字符)')

    # ─── 浏览器管理 ──────────────────────────────

    def _ensure_browser(self):
        """确保浏览器已启动并登录"""
        if self._page:
            return True
        cookie = self._load_cookie()
        if not cookie:
            return False
        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled', '--dns-servers=8.8.8.8']
        )
        self._context = self._browser.new_context(viewport={'width': 1280, 'height': 800})
        self._context.add_cookies([
            {'name': k.split('=')[0], 'value': k.split('=')[1],
             'domain': '.weread.qq.com', 'path': '/'}
            for k in cookie.split('; ')
        ])
        self._page = self._context.new_page()
        return True

    # ─── 登录 ──────────────────────────────────

    def login(self) -> bool:
        """
        弹出浏览器扫码登录。
        返回 True=登录成功, False=用户取消。
        """
        self._log('正在打开浏览器，请扫码登录微信读书...')
        try:
            p = sync_playwright().start()
            browser = p.chromium.launch(
                headless=False,
                args=['--disable-blink-features=AutomationControlled', '--dns-servers=8.8.8.8']
            )
        except Exception as e:
            self._log('无法打开浏览器进行登录')
            print()
            print('  原因：当前环境可能没有图形界面支持。')
            print()
            print('  解决方法：')
            print('    ① 在本地 WSL（有桌面环境的）运行一次:')
            print('       python weread_exporter.py --login')
            print('    ② 扫码登录后，cookie 和 API Key 会自动保存')
            print('    ③ 后续在 AI Agent 中可直接调用 --export / --skill')
            print()
            return False
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = ctx.new_page()
        page.goto('https://weread.qq.com', wait_until='domcontentloaded', timeout=30000)

        print()
        print('  ╔══════════════════════════════════════════════╗')
        print('  ║          请扫码登录微信读书                  ║')
        print('  ║                                            ║')
        print('  ║  ① 点击右上角「登录」                       ║')
        print('  ║  ② 使用微信扫码                            ║')
        print('  ║  ③ 在手机上确认                            ║')
        print('  ║                                            ║')
        print('  ║  登录成功后脚本会自动保存并继续              ║')
        print('  ║  关闭浏览器窗口可取消操作                    ║')
        print('  ╚══════════════════════════════════════════════╝')
        print()

        login_ok = False
        while True:
            time.sleep(1)
            try:
                url = page.url
                if 'web/shelf' in url or 'web/reader' in url:
                    login_ok = True
                    break
                has_avatar = page.evaluate("""() => {
                    return !!document.querySelector(
                        '.readerTopBar_avatar, .nav_user_avatar, [class*="avatar"]'
                    );
                }""")
                if has_avatar:
                    login_ok = True
                    break
            except:
                pass
            try:
                page.title()
            except:
                print()
                self._log('浏览器窗口已关闭')
                print()
                print('  [Y] 重新打开浏览器，扫码登录')
                print('  [N] 已退出，欢迎下次使用')
                choice = input('  请选择 (Y/N): ').strip().upper()
                if choice == 'Y':
                    browser.close()
                    p.stop()
                    return self.login()  # 递归重试
                else:
                    break

        if login_ok:
            self._log('登录成功！')
            cookies = ctx.cookies()
            cookie_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies])
            self._save_cookie(cookie_str)

            # ── 获取 API Key（必须） ──
            print()
            print('  ╔══════════════════════════════════════════════╗')
            print('  ║     请获取 API Key（必要性：章节边界判定）   ║')
            print('  ║                                            ║')
            print('  ║  本工具基于微信读书官方 Skill 生成，         ║')
            print('  ║  具有官方 Skill 的全部功能。按章导出时       ║')
            print('  ║  的章节边界判定（入口/出口检测）需要         ║')
            print('  ║  API Key 获取官方目录结构。                 ║')
            print('  ║                                            ║')
            print('  ║  操作步骤：                                 ║')
            print('  ║  ① 在浏览器中点击右上角头像                  ║')
            print('  ║  ② 点击「微信读书Skill」                    ║')
            print('  ║  ③ 首次使用→生成 API Key → 复制            ║')
            print('  ║  ④ 将 API Key 粘贴到下方输入框              ║')
            print('  ║                                            ║')
            print('  ║  输入 N 可跳过（但不支持按章导出）           ║')
            print('  ╚══════════════════════════════════════════════╝')
            print()
            # 导航到微信读书 Skill 页面
            try:
                page.goto('https://weread.qq.com/r/weread-skills',
                          wait_until='domcontentloaded', timeout=15000)
                time.sleep(3)
            except:
                pass  # 导航失败不阻塞，用户手动操作

            api_key = input('  请输入 API Key（粘贴后按 Enter）: ').strip()
            if api_key and api_key.upper() != 'N':
                env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        'config', '.env')
                os.makedirs(os.path.dirname(env_path), exist_ok=True)
                with open(env_path, 'w') as f:
                    f.write(f'WEREAD_API_KEY={api_key}\n')
                self._log(f'API Key 已保存至 config/.env ({len(api_key)} 字符)')
            else:
                self._log('跳过 API Key 配置，按章导出功能受限')
                print('  ⚠ 提示：需要 API Key 才能使用 --skill 按章导出功能。')
                print('    整本导出 (--export) 不受影响。')
                print()

            # 启动后台浏览器
            browser.close()
            p.stop()
            self._ensure_browser()
        else:
            self._log('已退出，欢迎下次使用')

        browser.close()
        p.stop()
        return login_ok

    def check_login(self) -> bool:
        """检查是否已有有效登录（cookie 存在即可）"""
        cookie = self._load_cookie()
        return bool(cookie)

    # ─── 书架 ──────────────────────────────────

    def list_books(self) -> list:
        """
        获取书架书籍列表。
        返回: [{'id': 'xxx', 'title': '书名'}, ...]
        """
        if not self._ensure_browser():
            raise RuntimeError('未登录，请先调用 login()')

        page = self._page
        self._log('正在获取书架...')
        page.goto('https://weread.qq.com/web/shelf',
                  wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)

        books = self._extract_books(page)

        # 懒加载：滚动到底部加载更多
        prev_count = len(books)
        scroll_attempts = 0
        while scroll_attempts < 20:
            page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            time.sleep(1.5)
            books = self._extract_books(page)
            if len(books) > prev_count:
                prev_count = len(books)
                scroll_attempts = 0
                self._log(f'  已加载 {len(books)} 本书...')
            else:
                scroll_attempts += 1
                if scroll_attempts >= 3:
                    break

        self._log(f'书架加载完成: {len(books)} 本书')
        return books

    def _extract_books(self, page) -> list:
        """从页面提取书籍列表（内部方法）"""
        return page.evaluate("""() => {
            var items = document.querySelectorAll('a[href*="web/reader"]');
            var seen = new Set();
            var results = [];
            for (var item of items) {
                var href = item.getAttribute('href') || '';
                var match = href.match(/reader\\/([a-zA-Z0-9]+)/);
                if (!match) continue;
                var bookId = match[1];
                if (seen.has(bookId)) continue;
                seen.add(bookId);
                var title = '';
                var titleEl = item.querySelector(
                    '[class*="title"], [class*="name"], .bookName, h3, h4'
                );
                if (titleEl) title = titleEl.textContent.trim();
                if (!title) title = item.textContent.trim().split('\\n')[0].trim();
                results.push({id: bookId, title: title || '(无标题)'});
            }
            return results;
        }""")

    # ─── 双 ID 解析 ─────────────────────────────

    def is_reader_id(self, book_id: str) -> bool:
        """判断 book_id 是否为 readerId 格式（非纯数字的编码字符串）"""
        return not book_id.isdigit() and len(book_id) > 20

    def _resolve_book_ids(self, book_id: str, book_title: str = None,
                          page=None) -> dict:
        """
        将任意格式的 book_id 解析为 {'reader_id', 'book_id', 'title', 'author', 'pay_type'}。

        解析顺序（API Key 可用时自动双 ID 补充）：
          1. 书架匹配（get_shelf_full → readerId + bookId）
          2. 搜索 API / get_book_info → 数字 bookId
          3. 页面版权文本提取（title/author）

        ⚠️ 浏览器搜索 readerId 不属于此方法职责。
           搜索来的书（不在书架）的 readerId 由 Agent（编排层）
           从浏览器搜索页获取后传入 --export。代码只负责：
           - readerId → 浏览器导航导出
           - bookId → API 查询（作者、目录、付费检测）

        参数:
            book_id: 传入的 ID（可能是 readerId 或 数字 bookId）
            book_title: 可选，书名（加快搜索）
            page: 可选，复用浏览器页面

        返回:
            dict: {'reader_id': ..., 'book_id': ..., 'title': ..., 'author': ..., 'pay_type': ...}
                  无法解析的字段为 None
        """
        result = {'reader_id': None, 'book_id': None,
                  'title': book_title or None, 'author': None, 'pay_type': None}
        _id = book_id.strip()
        is_rid = self.is_reader_id(_id)

        # readerId 格式：直接使用，不需要浏览器搜索（已经是最佳路径）
        if is_rid:
            result['reader_id'] = _id

        # ── ① 书架匹配 ──
        try:
            shelf = get_shelf_full()
            reader_to_book = {b.get('readerId'): b for b in shelf if b.get('readerId')}
            book_to_shelf = {str(b.get('bookId')): b for b in shelf if b.get('bookId')}
            # 传入的是 readerId
            if _id in reader_to_book:
                entry = reader_to_book[_id]
                result['reader_id'] = _id
                result['book_id'] = str(entry.get('bookId', ''))
                result['title'] = entry.get('title', book_title)
            # 传入的是 bookId
            elif _id in book_to_shelf:
                entry = book_to_shelf[_id]
                result['book_id'] = _id
                result['reader_id'] = entry.get('readerId')
                result['title'] = entry.get('title', book_title)
            # 书名模糊匹配
            elif book_title:
                for b in shelf:
                    st = b.get('title', '')
                    if book_title in st or st in book_title:
                        result['reader_id'] = b.get('readerId')
                        result['book_id'] = str(b.get('bookId', ''))
                        result['title'] = st
                        break
        except Exception:
            pass

        # ── ② API 获取 bookId（搜索 API 或 get_book_info）──
        api_key_available = bool(os.environ.get('WEREAD_API_KEY', ''))
        if not api_key_available:
            env_path = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), 'config', '.env')
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        if line.startswith('WEREAD_API_KEY='):
                            api_key_available = bool(line.strip().split('=', 1)[1])
                            break

        if api_key_available:
            try:
                if not result['book_id'] and _id.isdigit():
                    result['book_id'] = _id
                if result['book_id']:
                    info = get_book_info(result['book_id'])
                    result['title'] = info.get('title', result['title'])
                    result['author'] = info.get('author', result['author'])
            except Exception:
                pass
            # 搜索 API：用书名搜 bookId
            if not result['book_id'] and result['title']:
                try:
                    from config.official_api import search
                    sres = search(result['title'], count=5)
                    for res in sres.get('results', []):
                        for bk in res.get('books', []):
                            bi = bk.get('bookInfo', {})
                            if result['title'] in bi.get('title', '') or \
                               (book_title and book_title in bi.get('title', '')):
                                result['book_id'] = str(bi.get('bookId', ''))
                                result['author'] = bi.get('author', result['author'])
                                pt = bi.get('payType', None)
                                if pt is not None:
                                    result['pay_type'] = pt
                                break
                        if result['book_id']:
                            break
                except Exception:
                    pass

        # ── ③ 书名补充（从页面读取）──
        p = page or self._page
        if not result['title'] and p:
            try:
                t = p.evaluate(
                    '() => {var el = document.querySelector(".readerTopBar_title");'
                    'return el ? el.textContent.trim() : ""}')
                if t:
                    result['title'] = t
            except Exception:
                pass

        # ── ⑤ 作者补充（从版权页）──
        if not result['author'] and result['book_id']:
            try:
                info = get_book_info(result['book_id'])
                result['author'] = info.get('author', '')
            except Exception:
                pass

        return result

    # ─── 导出 ──────────────────────────────────

    def export_book(self, book_id: str, output_dir: str = None,
                    trial: str = None) -> str:
        """
        导出整本书。

        参数:
            book_id: 书籍ID
            output_dir: 输出目录（默认 output/）
            trial: 'y'=导出试读, 'n'=付费书跳过, None=自动决定（付费书导试读）

        返回:
            输出文件路径，若用户取消返回 None
        """
        if not self._ensure_browser():
            raise RuntimeError('未登录，请先调用 login()')

        page = self._page

        # 解析双 ID：readerId（浏览器导航） + bookId（API 调用）
        resolved = self._resolve_book_ids(book_id, page=page)
        reader_id = resolved.get('reader_id') or book_id
        api_book_id = resolved.get('book_id') or \
            (book_id if book_id.isdigit() else None)
        resolved_title = resolved.get('title')
        resolved_author = resolved.get('author')
        self._log(f'ID解析: reader_id={reader_id}, book_id={api_book_id}, '
                  f'作者={resolved_author}')

        self._log('打开阅读器...')
        page.goto(f'https://weread.qq.com/web/reader/{reader_id}',
                  wait_until='domcontentloaded', timeout=30000)
        time.sleep(8)

        # 关闭书友想法
        page.evaluate("""() => {
            var reviewBtn = document.querySelector('.showBookReviews');
            if (reviewBtn && reviewBtn.classList.contains('showBookReviews_active')) {
                reviewBtn.click();
            }
            var toggle = document.querySelector('.isNormalReader');
            if (toggle) { toggle.click(); }
        }""")
        time.sleep(1)
        self._log('书友想法已关闭')

        # 安装 Canvas Hook
        page.evaluate(CANVAS_HOOK_JS)
        page.evaluate('window._capturedTexts = []')
        self._log('Canvas Hook已安装')

        # 付费检测
        need_pay = page.evaluate(
            '() => !!document.querySelector(\'.need_pay_mask\')'
        )
        if need_pay:
            if trial == 'n':
                self._log('用户取消导出（付费书跳过）')
                return None
            elif trial == 'y':
                self._log('导出试读部分')
            else:
                # 自动模式：默认导试读
                self._log('本书需要付费会员才能阅读完整内容，自动导出试读部分')

        # 获取书名（优先用解析结果，再回退到页面读取）
        book_title = resolved_title or page.evaluate("""() => {
            var el = document.querySelector('.readerTopBar_title');
            return el ? el.textContent.trim() : '未命名书籍';
        }""")

        # 获取作者（优先级：resolve 结果 → API bookId → 搜索 API 用书名 → 版权页文本）
        book_author = resolved_author or ''
        if not book_author and api_book_id:
            try:
                info = get_book_info(api_book_id)
                book_author = info.get('author', '') or ''
            except Exception:
                pass
        if not book_author and book_title:
            # 用书名搜索 API 获取作者
            try:
                from config.official_api import search
                sres = search(book_title, count=5)
                for res in sres.get('results', []):
                    for bk in res.get('books', []):
                        bi = bk.get('bookInfo', {})
                        if book_title in bi.get('title', ''):
                            book_author = bi.get('author', book_author)
                            if not api_book_id:
                                api_book_id = str(bi.get('bookId', ''))
                                pt = bi.get('payType', None)
                                self._log(f'搜索API补充: book_id={api_book_id}, '
                                          f'作者={book_author}, payType={pt}')
                            break
                    if book_author:
                        break
            except Exception:
                pass

        # 导航到 TOC[1]（版权信息）
        page.mouse.click(10, 10)
        time.sleep(0.5)
        page.click('.readerControls_item.catalog', timeout=5000)
        time.sleep(2)
        self._log('导航到首页: TOC[1] (版权信息)')
        page.locator('.readerCatalog_list_item').nth(1).click(timeout=5000)
        time.sleep(3)
        page.mouse.click(10, 10)
        time.sleep(1)

        # 最终兜底：从版权信息页文本提取作者
        if not book_author:
            try:
                page_text = page.evaluate('window._capturedTexts') or []
                for line in page_text:
                    if isinstance(line, str) and line.startswith('作者：'):
                        book_author = line.replace('作者：', '').strip()
                        break
                page.evaluate('window._capturedTexts = []')
            except Exception:
                pass

        # 准备输出（书名 + 作者）
        author_suffix = f' - {book_author}' if book_author else ''
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', book_title + author_suffix)
        out_dir = output_dir or OUTPUT_DIR
        book_dir = os.path.join(out_dir, safe_title)
        os.makedirs(book_dir, exist_ok=True)
        out_file = os.path.join(book_dir, f'{safe_title}.md')

        self._log('开始导出...')
        print(f'  输出文件: {out_file}')

        all_lines = []
        total_pages = 0
        prev_raw = None
        same_count = 0
        empty_pages = 0

        # 首页强制重绘
        first_raw = page.evaluate('window._capturedTexts') or []
        page.evaluate('window._capturedTexts = []')
        if not first_raw:
            page.keyboard.press('ArrowRight')
            time.sleep(PAGE_SLEEP)
            page.evaluate('window._capturedTexts = []')
            page.keyboard.press('ArrowLeft')
            time.sleep(PAGE_SLEEP)
            first_raw = page.evaluate('window._capturedTexts') or []
            page.evaluate('window._capturedTexts = []')

        if first_raw:
            prev_raw = first_raw
            lines = _reassemble_page(first_raw)
            all_lines.extend(lines)
            all_lines.append('')
            self._log(f'  首页: {len(first_raw)} fillText calls')

        # 导出循环
        try:
            for page_num in range(1, 99999):
                page.evaluate('window._capturedTexts = []')
                page.keyboard.press('ArrowRight')
                time.sleep(PAGE_SLEEP)

                result = page.evaluate("""() => {
                    var texts = window._capturedTexts || [];
                    var paywall = false;
                    var el = document.querySelector('.need_pay_mask');
                    if (el && el.offsetParent !== null) paywall = true;
                    return {texts: texts, paywall: paywall};
                }""")
                raw = result['texts'] or []
                total_pages += 1

                if result['paywall']:
                    self._log('试读内容已导出完毕（付费墙）')
                    break

                if not raw:
                    empty_pages += 1
                    if empty_pages >= 8:
                        self._log('全书完（连续8页无内容）')
                        break
                    continue
                empty_pages = 0

                if prev_raw and _texts_identical(raw, prev_raw):
                    same_count += 1
                    if same_count >= CONSECUTIVE_EMPTY_LIMIT:
                        self._log('全书完')
                        break
                else:
                    same_count = 0

                if raw:
                    page_lines = _reassemble_page(raw)
                    if page_lines:
                        all_lines.extend(page_lines)
                        all_lines.append('')
                    prev_raw = raw

                if page_num % 50 == 0:
                    with open(out_file, 'w', encoding='utf-8') as f:
                        f.write(f'# {book_title}\n\n')
                        f.write('\n'.join(all_lines))

        except KeyboardInterrupt:
            if all_lines:
                with open(out_file, 'w', encoding='utf-8') as f:
                    f.write(f'# {book_title}\n\n')
                    f.write('\n'.join(all_lines))
                self._log(f'已保存已导出的 {total_pages} 页')
            return None

        # 最终写入
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write(f'# {book_title}\n\n')
            f.write('\n'.join(all_lines))

        size = os.path.getsize(out_file)
        self._log(f'导出完成: {out_file} ({size/1024:.1f} KB, {total_pages} 页)')
        return out_file

    def export_chapters(self, book_id: str, chapter_range: str = None,
                        output_dir: str = None, trial: str = None,
                        api_book_id: str = None) -> list:
        """
        Skill 模式：按一级标题逐章导出（每个章节一个文件）。

        流程:
          1. 打开阅读器 → 关闭书友想法 → 安装 Hook → 付费检测
          2. 提取 TOC → 分类 → 显示导引树
          3. 用户输入章节范围（如 "5-8" 或 "3"）
          4. 逐章导出（TOC-prev 导航 → ArrowRight 捕获 → 章节结束检测 → 保存文件）

        参数:
            book_id: 书籍ID
            chapter_range: '5-8' 格式，None 则交互式输入
            output_dir: 输出目录
            trial: 付费书策略

        返回:
            输出文件路径列表
        """
        if not self._ensure_browser():
            raise RuntimeError('未登录，请先调用 login()')

        # 检查 API Key（按章导出必须）
        api_key = os.environ.get('WEREAD_API_KEY', '')
        if not api_key:
            # 尝试从 config/.env 读取
            env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', '.env')
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        if line.startswith('WEREAD_API_KEY='):
                            api_key = line.strip().split('=', 1)[1]
                            break
        if not api_key:
            print()
            print('  ╔══════════════════════════════════════════════════════╗')
            print('  ║  按章导出需要 API Key 来获取官方目录结构             ║')
            print('  ║  没有 API Key 可以改用整本导出：                     ║')
            print('  ║      python weread_exporter.py --export <readerId>  ║')
            print('  ║  整本导出不需要 API Key，自上传书籍也支持。         ║')
            print('  ║  如需 API Key（按章导出），请先运行：               ║')
            print('  ║      python weread_exporter.py --login              ║')
            print('  ║  扫码后 → 个人中心 → 微信读书 Skill → 生成 Key     ║')
            print('  ╚══════════════════════════════════════════════════════╝')
            print()
            return []

        page = self._page
        self._log('打开阅读器...')
        page.goto(f'https://weread.qq.com/web/reader/{book_id}',
                  wait_until='domcontentloaded', timeout=30000)
        time.sleep(8)

        # 关闭书友想法
        page.evaluate("""() => {
            var reviewBtn = document.querySelector('.showBookReviews');
            if (reviewBtn && reviewBtn.classList.contains('showBookReviews_active')) {
                reviewBtn.click();
            }
            var toggle = document.querySelector('.isNormalReader');
            if (toggle) { toggle.click(); }
        }""")
        time.sleep(1)
        self._log('书友想法已关闭')

        # 安装 Canvas Hook
        page.evaluate(CANVAS_HOOK_JS)
        page.evaluate('window._capturedTexts = []')
        self._log('Canvas Hook已安装')

        # 付费检测
        need_pay = page.evaluate('() => !!document.querySelector(\'.need_pay_mask\')')
        if need_pay:
            if trial == 'n':
                self._log('用户取消导出（付费书跳过）')
                return []
            else:
                # 付费书且同意试读 → 直接复用整本试读导出，返回单文件列表
                fp = self.export_book(book_id, output_dir=output_dir, trial='y')
                if fp:
                    base, ext = os.path.splitext(fp)
                    trial_fp = f"{base}（试读部分）{ext}"
                    os.rename(fp, trial_fp)
                    return [trial_fp]
                return []
        # 获取书名
        book_title = page.evaluate("""() => {
            var el = document.querySelector('.readerTopBar_title');
            return el ? el.textContent.trim() : '未命名书籍';
        }""")

        # 获取作者（API 方式）
        book_author = ''
        try:
            info = get_book_info(book_id)
            book_author = info.get('author', '') or ''
        except Exception:
            try:
                shelf = get_shelf_full()
                for b in shelf:
                    if b.get('readerId') == book_id:
                        info = get_book_info(str(b['bookId']))
                        book_author = info.get('author', '') or ''
                        break
            except Exception:
                pass

        author_suffix = f' - {book_author}' if book_author else ''
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', book_title + author_suffix)
        out_dir = output_dir or OUTPUT_DIR
        book_dir = os.path.join(out_dir, safe_title)
        os.makedirs(book_dir, exist_ok=True)

        # 提取 TOC
        toc_texts = _extract_toc(page)
        if not toc_texts:
            self._log('无法提取目录，回退到整本导出')
            fp = self.export_book(book_id, output_dir, trial)
            return [fp] if fp else []

        # 分类
        classified = _classify_toc(toc_texts)

        # 构建 API TOC 对齐结构（用于 API+DOM 联合导航检测）
        aligned_chapters = []
        # 自动获取 api_book_id（书架书无需用户传 --api-id）
        if not api_book_id:
            try:
                shelf_books = get_shelf_full()
                # 优先精确匹配，退到子串匹配
                exact = None
                for sb in shelf_books:
                    st = sb.get('title', '')
                    if st == book_title:
                        exact = sb
                        break
                if exact:
                    api_book_id = exact.get('bookId')
                    self._log(f'书架精确匹配 api_book_id={api_book_id}')
                else:
                    for sb in shelf_books:
                        st = sb.get('title', '')
                        if book_title in st or st in book_title:
                            api_book_id = sb.get('bookId')
                            self._log(f'书架子串匹配 api_book_id={api_book_id}')
                            break
                if not api_book_id:# 书架未匹配 → 用搜索 API 按书名查（搜索来的书也自动获取）
                    self._log('书架未匹配，尝试搜索 API...')
                    try:
                        search_res = search(book_title, count=10)
                        candidates = []
                        for res in search_res.get('results', []):
                            for bk in res.get('books', []):
                                info = bk.get('bookInfo', {})
                                t = info.get('title', '')
                                bid = info.get('bookId')
                                if bid and (book_title in t or t in book_title):
                                    candidates.append((t, bid))
                        if candidates:
                            # 优先精确匹配
                            exact = [c for c in candidates if c[0] == book_title]
                            if exact:
                                api_book_id = exact[0][1]
                                self._log(f'搜索精确匹配 api_book_id={api_book_id}')
                            else:
                                # 退到子串匹配，用最短距离（标题长度最接近的）
                                candidates.sort(key=lambda c: abs(len(c[0]) - len(book_title)))
                                api_book_id = candidates[0][1]
                                self._log(f'搜索近似匹配 api_book_id={api_book_id} (title="{candidates[0][0]}")')
                        else:
                            self._log('搜索 API 也未匹配到，使用 DOM 分类（如需精确范围请传 --api-id）')
                    except Exception as e2:
                        self._log(f'搜索 API 匹配失败: {e2}')
            except Exception as e:
                self._log(f'自动获取 api_book_id 失败，回退 DOM 分类: {e}')

        # API TOC 覆盖：用官方 API 的 level 字段修正 DOM 分类
        if api_book_id:
            try:
                api_chapters = get_chapter_list(api_book_id)
                # 建立 API 标题 → level 映射（标题匹配而非索引对应）
                api_level_map = {}
                for ch in api_chapters:
                    title = ch.get('title', '').strip()
                    level = ch.get('level', 2)  # 缺省 level 时默认为二级
                    if title:
                        api_level_map[title] = '一级' if level == 1 else '二级'
                if not api_level_map:
                    self._log('API TOC 无有效标题层级，保留 DOM 分类')
                else:
                    matched = 0
                    for c in classified:
                        title = c['text'].strip()
                        if title in api_level_map:
                            c['level'] = api_level_map[title]
                            matched += 1
                        else:
                            # 尝试子串匹配（DOM 可能截断或前后有空格差异）
                            found = False
                            for api_title, api_level in api_level_map.items():
                                if title and (title in api_title or api_title in title):
                                    c['level'] = api_level
                                    matched += 1
                                    found = True
                                    break
                            if not found:
                                c['level'] = '二级'  # 未匹配视为细粒度子节
                    l1_count = sum(1 for c in classified if c['level'] == '一级')
                    self._log(f'API TOC 匹配 {matched}/{len(classified)} 条（含 {l1_count} 个一级）')
                    # 构建 API ↔ DOM 对齐（用于 API+DOM 联合导航）
                    aligned_chapters = _align_api_dom_toc(api_chapters, classified)
                    api_l1_count = sum(1 for a in aligned_chapters if a is not None)
                    self._log(f'API↔DOM 对齐: {api_l1_count} 个一级章节')
            except Exception as e:
                self._log(f'API TOC 获取失败，回退 DOM 分类: {e}')

        # 渲染导引树
        guide_tree, max_num = _render_guide_tree(classified)
        print()
        print('  ╔══════════════════════════════════════════════╗')
        print('  ║         导引树（按一级标题导出）              ║')
        print('  ╚══════════════════════════════════════════════╝')
        print(guide_tree)
        print()
        print(f'  共 {max_num} 个一级标题')

        # 获取一级标题索引列表
        first_level_indices = [c['index'] for c in classified if c['level'] == '一级']

        # 确定章节范围
        if chapter_range:
            parts = chapter_range.split('-')
            try:
                ch_start_num = int(parts[0])
                ch_end_num = int(parts[1]) if len(parts) > 1 else ch_start_num
            except (ValueError, IndexError):
                self._log(f'章节范围格式无效: {chapter_range}，使用全部')
                ch_start_num = 1
                ch_end_num = max_num
        else:
            # 交互式输入
            try:
                raw = input(f'\n  请输入要导出的章节范围（如 1-{max_num}，或输入单个数字）: ').strip()
                if '-' in raw:
                    parts = raw.split('-')
                    ch_start_num = int(parts[0])
                    ch_end_num = int(parts[1])
                else:
                    ch_start_num = int(raw)
                    ch_end_num = ch_start_num
            except (ValueError, EOFError):
                self._log('输入无效，使用全部章节')
                ch_start_num = 1
                ch_end_num = max_num

        # 验证范围
        ch_start_num = max(1, min(ch_start_num, max_num))
        ch_end_num = max(ch_start_num, min(ch_end_num, max_num))

        # 转换为 TOC 索引
        first_level_list = first_level_indices
        total_first = len(first_level_list)
        if ch_start_num > total_first:
            self._log('章节范围无效')
            return []
        ch_start = first_level_list[ch_start_num - 1]
        ch_end = first_level_list[min(ch_end_num, total_first) - 1]

        self._log(f'导出章节范围: {ch_start_num}-{ch_end_num} (TOC索引 {ch_start}-{ch_end})')

        # 逐章导出
        output_files = []
        # 构建对齐信息（API+DOM 联合导航）

        # ── 导航到第一个目标章节的前一章（一次性） ──
        first_ch_info = aligned_chapters[ch_start_num - 1] if aligned_chapters else None
        if first_ch_info and first_ch_info.get('dom_index') is not None:
            nav_dom_idx = max(0, first_ch_info['dom_index'] - 1)
        else:
            nav_dom_idx = max(0, first_level_list[ch_start_num - 1] - 1)
        self._log(f'DOM 导航到 [{nav_dom_idx}] → ArrowRight...')
        page.mouse.click(10, 10)
        time.sleep(0.5)
        page.click('.readerControls_item.catalog', timeout=5000)
        time.sleep(2)
        page.locator('.readerCatalog_list_item').nth(nav_dom_idx).click(timeout=5000)
        time.sleep(3)
        page.mouse.click(10, 10)
        time.sleep(1)
        page.evaluate('window._capturedTexts = []')

        # ── 流式导出：所有章节共享一个 ArrowRight 循环 ──
        ch_lines = []
        total_pages = 0
        chapter_entered = False
        ch_page_num = 0
        first_page_side = 'all'
        prev_raw = None
        same_count = 0
        empty_pages = 0
        page_buffer = []
        ch_lines_len_before_append = 0

        active_ch_idx = ch_start_num - 1       # 当前章节在 range 内的索引
        max_ch = min(ch_end_num, total_first) # 最后一个章节

        # 初始化当前章节
        # 初始化当前章节（流式导出：切换时调用）
        def _setup_chapter(ch_i):
            nonlocal ch_lines, chapter_entered, first_page_side, prev_raw
            nonlocal same_count, empty_pages
            ch_lines = []
            chapter_entered = False
            first_page_side = 'all'
            prev_raw = None
            same_count = 0
            empty_pages = 0
            toc_start = first_level_list[ch_i]
            ct = classified[toc_start]['text']
            safe_ch = re.sub(r'[\\/:*?"<>|]', '_', ct)[:80]
            out_fp = os.path.join(book_dir, f'{safe_ch}.md')
            ch_i_info = aligned_chapters[ch_i] if ch_i < len(aligned_chapters) else None

            # 入口检测标题
            et = [ct]
            first_sub = None
            if ch_i_info and ch_i_info['sub_titles']:
                first_sub = ch_i_info['sub_titles'][0]
                et.append(first_sub)

            # 出口检测标题
            next_et = None
            if ch_i_info and ch_i + 1 < len(aligned_chapters) and aligned_chapters[ch_i + 1] is not None:
                next_ch = aligned_chapters[ch_i + 1]
                next_et = [next_ch['api_title']]
                if next_ch['sub_titles']:
                    next_et.extend(next_ch['sub_titles'])

            sub_count = len(ch_i_info['sub_titles']) if ch_i_info else 0
            max_pages = max(300, sub_count * 200 + 50)

            self._log(f'导出章节 [{ch_i - ch_start_num + 2}/{ch_end_num - ch_start_num + 1}]: {ct}')
            self._log(f'  ch_i={ch_i} ch_i_info={"有" if ch_i_info else "无"} aligned_len={len(aligned_chapters)} next_et={next_et}')
            print(f'  输出: {out_fp}')
            return ct, out_fp, ch_i_info, et, first_sub, next_et, max_pages

        ch_title, out_file, ch_info, entry_titles, first_sub_title, next_entry_titles, max_chapter_pages = _setup_chapter(active_ch_idx)

        try:
            for page_num in range(1, 99999):
                # 页面操作可能因浏览器崩溃而失败，捕获异常后保存进度
                try:
                    page.evaluate('window._capturedTexts = []')
                    page.keyboard.press('ArrowRight')
                    time.sleep(PAGE_SLEEP)

                    result = page.evaluate('''() => {
                        var texts = window._capturedTexts || [];
                        var paywall = false;
                        var el = document.querySelector('.need_pay_mask');
                        if (el && el.offsetParent !== null) paywall = true;
                        return {texts: texts, paywall: paywall};
                    }''')
                except Exception as e:
                    self._log(f'页面操作异常: {e}')
                    # 保存当前章节进度
                    if ch_lines and out_file not in output_files:
                        with open(out_file, 'w', encoding='utf-8') as f:
                            f.write(f'# {ch_title}\n\n')
                            f.write('\n'.join(ch_lines))
                        size = os.path.getsize(out_file)
                        self._log(f'异常时已保存: {out_file} ({size/1024:.1f} KB)')
                        output_files.append(out_file)
                    break
                raw = result['texts'] or []
                total_pages += 1
                ch_page_num += 1

                page_buffer.append(raw)
                if len(page_buffer) > 4:
                    page_buffer.pop(0)

                if result['paywall']:
                    self._log('试读结束（付费墙）')
                    break

# ── 入口检测（Canvas 文字匹配章节标题） ──
                if ch_info and not chapter_entered:
                    current_dom_title = _get_chapter_title(page)
                    entered = False

                    # 确认DOM变了：DOM标题匹配目标章节
                    if current_dom_title:
                        for et in entry_titles:
                            net = _normalize_text(et)
                            ndom = _normalize_text(current_dom_title)
                            if ndom and (net == ndom or net in ndom or ndom in net):
                                entered = True
                                break

                    if entered:
                        if _canvas_matches_title(raw, ch_title):
                            # 【情况①】Canvas匹配 → 标题在左页
                            # 左右都是新章节内容，直接导出两侧
                            chapter_entered = True
                            self._log(f'进入章节: {ch_title} (Canvas匹配)')
                            first_page_side = 'all'
                            ch_lines_len_before_append = len(ch_lines)
                            page_lines = _reassemble_page(raw)
                            if page_lines:
                                ch_lines.extend(page_lines)
                                ch_lines.append('')
                            prev_raw = raw
                        else:
                            # Canvas不匹配 → ArrowLeft回退到过渡屏
                            self._log('入口检测: DOM变了但Canvas未匹配，ArrowLeft回退')
                            page.evaluate('window._capturedTexts = []')
                            page.keyboard.press('ArrowLeft')
                            time.sleep(PAGE_SLEEP)
                            check_raw = page.evaluate(
                                '() => { var t = window._capturedTexts || []; '
                                'window._capturedTexts = []; return t; }'
                            ) or []

                            if _canvas_matches_title(check_raw, ch_title):
                                # 【②-a-1】标题在过渡屏右页
                                # 从右页开始导出（左页是上一章尾）
                                chapter_entered = True
                                self._log(f'进入章节: {ch_title} (过渡屏右侧匹配)')
                                first_page_side = 'right'
                                ch_lines_len_before_append = len(ch_lines)
                                right_lines = _reassemble_page(check_raw, side='right')
                                if right_lines:
                                    ch_lines.extend(right_lines)
                                    ch_lines.append('')
                                prev_raw = check_raw
                            else:
                                # 【②-a-2】图片标题
                                # ArrowRight前进一页 → 从左边页导出
                                page.keyboard.press('ArrowRight')
                                time.sleep(PAGE_SLEEP)
                                chapter_entered = True
                                self._log(f'进入章节: {ch_title} (图片标题，从左边页开始)')
                                first_page_side = 'left'
                                new_raw = page.evaluate(
                                    '() => { var t = window._capturedTexts || []; '
                                    'window._capturedTexts = []; return t; }'
                                ) or []
                                ch_lines_len_before_append = len(ch_lines)
                                if new_raw:
                                    page_lines = _reassemble_page(new_raw)
                                    if page_lines:
                                        ch_lines.extend(page_lines)
                                        ch_lines.append('')
                                    prev_raw = new_raw
                    continue

                # 入口检测（fallback：无 API 信息）
                if not ch_info and not chapter_entered:
                    chapter_entered = True
                    first_page_side = 'all'
                    if raw:
                        ch_lines_len_before_append = len(ch_lines)
                        page_lines = _reassemble_page(raw)
                        if page_lines:
                            ch_lines.extend(page_lines)
                            ch_lines.append('')
                        prev_raw = raw
                    continue

# ── 出口检测（Canvas 文字匹配下一章节标题） ──
                if chapter_entered and next_entry_titles:
                    current_dom_title = _get_chapter_title(page)
                    exit_signalled = False
                    next_title = next_entry_titles[0] if next_entry_titles else ''
                    self._log(f'[出口检测] DOM={current_dom_title!r} next={next_title!r} entry={entry_titles[:3]}')

                    # 确认DOM变了：DOM标题匹配下一章节（含全部子章节标题）
                    if current_dom_title and any(
                        _normalize_text(nt) == _normalize_text(current_dom_title)
                        or _normalize_text(nt) in _normalize_text(current_dom_title)
                        or _normalize_text(current_dom_title) in _normalize_text(nt)
                        for nt in next_entry_titles
                    ):
                        exit_signalled = True

                    if exit_signalled:
                        if _canvas_matches_title(raw, next_title):
                            # 【出口①】Canvas匹配 → 上页全是原章内容
                            # 当前页的内容（raw）尚未写入ch_lines → ch_lines即原章全部内容
                            self._log(f'章节结束: {ch_title} (Canvas匹配下章: {next_title})')
                            with open(out_file, 'w', encoding='utf-8') as f:
                                f.write(f'# {ch_title}\n\n')
                                f.write('\n'.join(ch_lines))
                            size = os.path.getsize(out_file)
                            self._log(f'已完成: {out_file} ({size/1024:.1f} KB)')
                            output_files.append(out_file)

                            # 切换到下一章，当前raw是下一章内容
                            active_ch_idx += 1
                            if active_ch_idx >= max_ch:
                                break
                            ch_title, out_file, ch_info, entry_titles, first_sub_title, next_entry_titles, max_chapter_pages = _setup_chapter(active_ch_idx)
                            page_buffer = []
                            ch_page_num = 0
                            chapter_entered = True
                            ch_lines_len_before_append = len(ch_lines)
                            next_lines = _reassemble_page(raw)
                            if next_lines:
                                ch_lines.extend(next_lines)
                                ch_lines.append('')
                            prev_raw = raw

                        else:
                            # Canvas不匹配 → ArrowLeft回退到过渡屏
                            self._log('出口检测: DOM变了但Canvas未匹配下章，ArrowLeft回退')
                            page.evaluate('window._capturedTexts = []')
                            page.keyboard.press('ArrowLeft')
                            time.sleep(PAGE_SLEEP)
                            check_raw = page.evaluate(
                                '() => { var t = window._capturedTexts || []; '
                                'window._capturedTexts = []; return t; }'
                            ) or []

                            if _canvas_matches_title(check_raw, next_title):
                                # 【出口②】过渡屏右侧有下章标题
                                # 原章保留至左页：回退至过渡屏写入前，只追加左页
                                self._log(f'章节结束: {ch_title} (过渡屏右侧匹配下章)')
                                ch_lines = ch_lines[:ch_lines_len_before_append]
                                left_lines = _reassemble_page(check_raw, side='left')
                                if left_lines:
                                    ch_lines.extend(left_lines)
                                    ch_lines.append('')

                                with open(out_file, 'w', encoding='utf-8') as f:
                                    f.write(f'# {ch_title}\n\n')
                                    f.write('\n'.join(ch_lines))
                                size = os.path.getsize(out_file)
                                self._log(f'已完成: {out_file} ({size/1024:.1f} KB)')
                                output_files.append(out_file)

                                # 过渡屏右页是下章开头
                                active_ch_idx += 1
                                if active_ch_idx >= max_ch:
                                    break
                                ch_title, out_file, ch_info, entry_titles, first_sub_title, next_entry_titles, max_chapter_pages = _setup_chapter(active_ch_idx)
                                page_buffer = []
                                ch_page_num = 0
                                chapter_entered = True
                                ch_lines_len_before_append = len(ch_lines)
                                right_lines = _reassemble_page(check_raw, side='right')
                                if right_lines:
                                    ch_lines.extend(right_lines)
                                    ch_lines.append('')
                                prev_raw = check_raw

                            else:
                                # 【出口③】过渡屏也无匹配 → 图片标题
                                # 原章保留过渡屏左右两页（图片标题页内容为空，不影响）
                                self._log(f'章节结束: {ch_title} (图片标题)')
                                with open(out_file, 'w', encoding='utf-8') as f:
                                    f.write(f'# {ch_title}\n\n')
                                    f.write('\n'.join(ch_lines))
                                size = os.path.getsize(out_file)
                                self._log(f'已完成: {out_file} ({size/1024:.1f} KB)')
                                output_files.append(out_file)

                                # ArrowRight前进到下一屏
                                page.keyboard.press('ArrowRight')
                                time.sleep(PAGE_SLEEP)

                                active_ch_idx += 1
                                if active_ch_idx >= max_ch:
                                    break
                                ch_title, out_file, ch_info, entry_titles, first_sub_title, next_entry_titles, max_chapter_pages = _setup_chapter(active_ch_idx)
                                page_buffer = []
                                ch_page_num = 0
                                chapter_entered = True
                                first_page_side = 'left'
                                ch_lines_len_before_append = len(ch_lines)
                                new_raw = page.evaluate(
                                    '() => { var t = window._capturedTexts || []; '
                                    'window._capturedTexts = []; return t; }'
                                ) or []
                                if new_raw:
                                    page_lines = _reassemble_page(new_raw)
                                    if page_lines:
                                        ch_lines.extend(page_lines)
                                        ch_lines.append('')
                                    prev_raw = new_raw
                        continue

                # 空页处理
                if not raw:
                    empty_pages += 1
                    if empty_pages >= 8:
                        self._log('全书完（连续8页无内容）')
                        break
                    continue
                empty_pages = 0

                # 内容重复检测
                if prev_raw and _texts_identical(raw, prev_raw):
                    same_count += 1
                    if same_count >= CONSECUTIVE_EMPTY_LIMIT:
                        self._log('章节结束（内容重复）')
                        break
                else:
                    same_count = 0

                # 写入内容
                if raw and chapter_entered:
                    ch_lines_len_before_append = len(ch_lines)
                    page_lines = _reassemble_page(raw)
                    if page_lines:
                        ch_lines.extend(page_lines)
                        ch_lines.append('')
                    prev_raw = raw

                # 章节页数上限（防止出口检测漏掉时无限翻页）
                if chapter_entered and ch_page_num > max_chapter_pages:
                    self._log(f'章节结束（达页数上限 {max_chapter_pages}）: {ch_title}')
                    break

                if page_num % 50 == 0 and ch_lines:
                    with open(out_file, 'w', encoding='utf-8') as f:
                        f.write(f'# {ch_title}\n\n')
                        f.write('\n'.join(ch_lines))

            # 保存最后一个章节
            if ch_lines and out_file not in output_files:
                with open(out_file, 'w', encoding='utf-8') as f:
                    f.write(f'# {ch_title}\n\n')
                    f.write('\n'.join(ch_lines))
                size = os.path.getsize(out_file)
                self._log(f'已完成: {out_file} ({size/1024:.1f} KB)')
                output_files.append(out_file)

        except KeyboardInterrupt:
            if ch_lines:
                with open(out_file, 'w', encoding='utf-8') as f:
                    f.write(f'# {ch_title}\n\n')
                    f.write('\n'.join(ch_lines))
                self._log(f'已保存 {len(output_files)} 个章节文件')
            return output_files

        # 关闭页面释放资源
        try:
            page.close()
        except:
            pass
        self._log('全部完成，歇 8 秒释放资源...')
        time.sleep(8)

        self._log(f'全部导出完成: {len(output_files)} 个章节文件')
        return output_files

    # ─── 清理 ──────────────────────────────────

    def close(self):
        """关闭浏览器，释放资源"""
        try:
            if self._browser:
                self._browser.close()
        except:
            pass
        try:
            if self._p:
                self._p.stop()
        except:
            pass
        self._page = None
        self._browser = None
        self._p = None


# ========== 命令行入口（快速测试用） ==========

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='微信读书导出工具 - API 测试')
    parser.add_argument('--list', action='store_true', help='列出书架')
    parser.add_argument('--export', type=str, help='导出书籍（book_id）')
    parser.add_argument('--trial', choices=['y', 'n'], help='付费书试读策略')
    parser.add_argument('--verbose', action='store_true', help='显示详细日志')
    args = parser.parse_args()

    exporter = WeReadExporter(headless=True, verbose=args.verbose)

    if not exporter.check_login():
        print('未登录，请先运行交互式版本进行登录')
        sys.exit(1)

    if args.list:
        books = exporter.list_books()
        print(json.dumps(books, ensure_ascii=False, indent=2))
    elif args.export:
        filepath = exporter.export_book(args.export, trial=args.trial)
        if filepath:
            print(filepath)
    else:
        parser.print_help()

    exporter.close()