#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《火眼金睛读财报》- 单章导出测试 v2
修正：导航过程中逐页清空缓冲区，确保第一页内容正确捕获
"""
import sys, time, os, json, re, random
from collections import defaultdict, Counter
sys.path.insert(0, '/home/maxchen/weread-canvas-exporter')
from playwright.sync_api import sync_playwright
from auth import load_cookie

# ========== 配置 ==========
BOOK_ID = "e3832250813ab6fe5g01223b"
BOOK_NAME = "火眼金睛读财报"
TARGET_FIRST_LEVEL = 5

OUTPUT_DIR = "/home/maxchen/weread-canvas-exporter/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)

# ========== Canvas Hook ==========
CANVAS_HOOK_JS = """
() => {
    const origFill = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function() {
        var canvas = this.canvas;
        var rect = canvas ? canvas.getBoundingClientRect() : null;
        if (!rect || rect.width === 0) return origFill.apply(this, arguments);
        if (!window.wereadCapturedTexts) window.wereadCapturedTexts = [];
        window.wereadCapturedTexts.push({
            text: arguments[0],
            x: Math.round(arguments[1]) + Math.round(rect.left),
            y: Math.round(arguments[2])
        });
        return origFill.apply(this, arguments);
    };
}
"""

def install_hook(page):
    page.evaluate(CANVAS_HOOK_JS)
    page.evaluate('window.wereadCapturedTexts = []')
    log('Canvas Hook已安装')

def get_chapter_title(page):
    return page.evaluate('''() => {
        var el = document.querySelector('.renderTargetPageInfo_header_chapterTitle');
        return el ? el.textContent.trim() : '';
    }''')

# ========== TOC 分类 ==========
def get_number_level(text):
    if re.match(r'^第[一二三四五六七八九十百千\d]+[篇部部分]', text):
        return 1
    if re.match(r'^第[一二三四五六七八九十百千\d]+[章]', text):
        return 2
    if re.match(r'^第[一二三四五六七八九十百千\d]+[节]', text):
        return 3
    if re.match(r'^[一二三四五六七八九十]+[、．.]', text):
        return 4
    if re.match(r'^\d+[、．.]', text):
        return 5
    if re.match(r'^[(（]?\d+[)）]', text):
        return 6
    if re.match(r'^「\d+」', text):
        return 7
    if re.match(r'^【\d+】', text):
        return 8
    return 99

def classify_toc(toc):
    texts = [t['text'] for t in toc]
    numbered = [t for t in toc if get_number_level(t['text']) < 99]
    top_level = min(n['num_level'] for n in [{'num_level': get_number_level(t['text'])} for t in numbered]) if numbered else 99

    classified = []
    for t in toc:
        num_level = get_number_level(t['text'])
        if num_level < 99:
            if num_level == top_level:
                level = '一级'
            elif num_level == top_level + 1:
                level = '二级'
            elif num_level == top_level + 2:
                level = '三级'
            else:
                level = '四级'
        else:
            level = '未分类'
        classified.append({'index': t['index'], 'text': t['text'], 'level': level})

    # 纯文字标题处理
    known_first = ['扉页', '版权信息', '前言', '序', '自序', '推荐序', '后记',
                   '参考文献', '附录', '致谢', '引言', '导论', '关于作者']
    first_level_indices = [c['index'] for c in classified if c['level'] == '一级']

    for c in classified:
        if c['level'] != '未分类':
            continue
        text = c['text']
        idx = c['index']
        if text in known_first:
            c['level'] = '一级'
            continue
        if texts.count(text) > 1:
            c['level'] = '二级'
            continue
        prev_first = next((fi for fi in reversed(first_level_indices) if fi < idx), None)
        next_first = next((fi for fi in first_level_indices if fi > idx), None)
        if prev_first is not None and next_first is not None:
            c['level'] = '一级'
        elif prev_first is None:
            c['level'] = '一级'
        elif next_first is None:
            c['level'] = '一级'
        else:
            c['level'] = '一级'

    return classified

def render_guide_tree(classified):
    lines = []
    first_level_num = 0
    for c in classified:
        text = c['text']
        level = c['level']
        if level == '一级':
            first_level_num += 1
            lines.append(f"\n{first_level_num} --- {text}")
        elif level == '二级':
            lines.append(f"    +-- {text}")
        elif level == '三级':
            lines.append(f"    |   +-- {text}")
        elif level == '四级':
            lines.append(f"    |   |   +-- {text}")
        else:
            lines.append(f"    ?-- {text}")
    return '\n'.join(lines), first_level_num

# ========== 文本重组 ==========
def reassemble_page_text(texts):
    lines_dict = defaultdict(list)
    for t in texts:
        matched = False
        for existing_y in list(lines_dict.keys()):
            if abs(existing_y - t['y']) <= 0:
                lines_dict[existing_y].append(t)
                matched = True
                break
        if not matched:
            lines_dict[t['y']] = [t]

    left_lines = []
    right_lines = []
    for y in sorted(lines_dict.keys()):
        items = sorted(lines_dict[y], key=lambda t: t['x'])
        left_items = [t for t in items if t['x'] < 600]
        right_items = [t for t in items if t['x'] >= 600]
        # 注意：左右栏可能在同一Y坐标（正常正文对齐，不是页面头）
        # 不能跳过整行——应独立处理左右栏
        left_text = ''.join(t['text'] for t in left_items).strip()
        right_text = ''.join(t['text'] for t in right_items).strip()
        if left_text:
            left_lines.append(left_text)
        if right_text:
            right_lines.append(right_text)

    result = list(left_lines)
    if left_lines and right_lines:
        result.append('')
    result.extend(right_lines)
    return result

# ========== 主流程 ==========
def main():
    cookie = load_cookie()
    if not cookie:
        log('No cookie found')
        return

    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(viewport={'width': 1280, 'height': 800})
    ctx.add_cookies([
        {'name': k.split('=')[0], 'value': k.split('=')[1], 'domain': '.weread.qq.com', 'path': '/'}
        for k in cookie.split('; ')
    ])
    page = ctx.new_page()

    log('Loading page...')
    page.goto(f'https://weread.qq.com/web/reader/{BOOK_ID}', wait_until='domcontentloaded', timeout=30000)
    time.sleep(10)
    install_hook(page)

    # Extract TOC
    log('Extracting TOC...')
    page.mouse.click(10, 10)
    time.sleep(0.5)
    page.click('.readerControls_item.catalog', timeout=5000)
    time.sleep(3)

    toc = page.evaluate('''() => {
        var items = document.querySelectorAll('.readerCatalog_list_item');
        return Array.from(items).map((item, i) => ({
            index: i,
            text: (item.querySelector('.readerCatalog_list_item_title_text') || {}).textContent?.trim() || item.textContent.trim()
        }));
    }''')

    log(f'TOC items: {len(toc)}')
    for t in toc:
        print(f'  [{t["index"]:2d}] {t["text"]}', flush=True)

    classified = classify_toc(toc)
    tree_str, total_first = render_guide_tree(classified)

    print(f'\n{"="*60}', flush=True)
    print(f'Guide Tree ({total_first} first-level items)', flush=True)
    print(f'{"="*60}', flush=True)
    print(tree_str, flush=True)

    # Find target chapter
    first_count = 0
    target_toc_idx = None
    target_text = None
    for c in classified:
        if c['level'] == '一级':
            first_count += 1
            if first_count == TARGET_FIRST_LEVEL:
                target_toc_idx = c['index']
                target_text = c['text']
                break

    if target_toc_idx is None:
        log(f'Target #{TARGET_FIRST_LEVEL} not found')
        browser.close()
        p.stop()
        return

    log(f'Target: #{TARGET_FIRST_LEVEL} -> [{target_toc_idx}] "{target_text}"')

    # Find end boundary
    end_toc_idx = len(toc)
    for c in classified:
        if c['level'] == '一级' and c['index'] > target_toc_idx:
            end_toc_idx = c['index']
            log(f'  End boundary: [{end_toc_idx}] "{c["text"]}"')
            break

    # ========== Navigate ==========
    prev_toc_idx = max(0, target_toc_idx - 1)
    prev_text = toc[prev_toc_idx]['text']

    log(f'Navigate: TOC[{prev_toc_idx}] -> ArrowRight -> target')
    page.mouse.click(10, 10)
    time.sleep(0.5)
    page.click('.readerControls_item.catalog', timeout=5000)
    time.sleep(2)
    page.locator('.readerCatalog_list_item').nth(prev_toc_idx).click(timeout=5000)
    log(f'  Clicked TOC[{prev_toc_idx}]')
    time.sleep(4)
    page.mouse.click(10, 10)
    time.sleep(1)

    # ArrowRight to target, clearing buffer between pages
    current = get_chapter_title(page)
    log(f'  Current title: "{current}"')

    for step in range(50):
        current = get_chapter_title(page)
        if target_text in current or current in target_text:
            log(f'  Reached target (step {step+1})')
            break
        # Clear buffer BEFORE ArrowRight to discard previous page's fillText
        page.evaluate('window.wereadCapturedTexts = []')
        page.keyboard.press('ArrowRight')
        time.sleep(1.5)
        if step % 5 == 0:
            t = get_chapter_title(page)[:30]
            log(f'  Advancing... current: "{t}"')

    # Buffer now has ONLY the first page of target chapter
    log('  Capturing first page of target chapter')

    # ========== Export ==========
    log(f'Exporting "{target_text}"...')

    all_lines = []
    total_pages = 0
    consecutive_empty = 0
    reached_end = False
    pending_raw = None

    # Capture initial page
    initial_raw = page.evaluate('''() => {
        var t = window.wereadCapturedTexts || [];
        window.wereadCapturedTexts = [];
        return t;
    }''')
    if initial_raw:
        pending_raw = initial_raw
        log(f'  Initial page pending: {len(initial_raw)} fillText calls')
    else:
        log('  WARNING: Initial page has no fillText data!')

    for page_num in range(1, 500):
        page.keyboard.press('ArrowRight')
        total_pages += 1
        time.sleep(random.uniform(1.5, 2.5))

        current_raw = page.evaluate('''() => {
            var t = window.wereadCapturedTexts || [];
            window.wereadCapturedTexts = [];
            return t;
        }''')

        current_title = get_chapter_title(page)

        is_past_target = False
        if current_title:
            for c in classified:
                if c['level'] == '一级' and c['index'] > target_toc_idx:
                    if current_title == c['text'] or current_title in c['text'] or c['text'] in current_title:
                        is_past_target = True
                        break

        if is_past_target:
            if pending_raw:
                left_items = [t for t in pending_raw if t['x'] < 600]
                if left_items:
                    left_dict = defaultdict(list)
                    for t in left_items:
                        matched = False
                        for existing_y in list(left_dict.keys()):
                            if abs(existing_y - t['y']) <= 0:
                                left_dict[existing_y].append(t)
                                matched = True
                                break
                        if not matched:
                            left_dict[t['y']] = [t]
                    left_result = []
                    for y in sorted(left_dict.keys()):
                        items = sorted(left_dict[y], key=lambda t: t['x'])
                        text = ''.join(t['text'] for t in items).strip()
                        if text:
                            left_result.append(text)
                    if left_result:
                        all_lines.extend(left_result)
                        all_lines.append('')
                        log(f'  Boundary page: kept left column ({len(left_result)} lines)')
            log(f'  Chapter change detected: "{current_title}"')
            reached_end = True
            break

        if pending_raw:
            page_lines = reassemble_page_text(pending_raw)
            if page_lines:
                all_lines.extend(page_lines)
                all_lines.append('')

        if current_raw:
            pending_raw = current_raw
            consecutive_empty = 0
        else:
            pending_raw = None
            consecutive_empty += 1
            if consecutive_empty > 10:
                log('  10 consecutive empty pages, stopping')
                break

        if page_num % 20 == 0:
            t = current_title[:30] if current_title else '?'
            log(f'  Progress: page {total_pages}, current: "{t}"')

    if not reached_end and pending_raw:
        page_lines = reassemble_page_text(pending_raw)
        if page_lines:
            all_lines.extend(page_lines)
            all_lines.append('')

    # Save
    book_output_dir = os.path.join(OUTPUT_DIR, BOOK_NAME)
    os.makedirs(book_output_dir, exist_ok=True)

    safe_name = target_text.replace('/', '_').replace('\\', '_').replace(':', '_').replace('?', '').replace('"', '').strip()
    output_path = os.path.join(book_output_dir, f'{safe_name}.md')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f'# {target_text}\n\n')
        f.write('\n'.join(all_lines))

    file_size = os.path.getsize(output_path)
    log(f'Done!')
    log(f'  File: {output_path}')
    log(f'  Size: {file_size/1024:.1f} KB')
    log(f'  Pages: {total_pages}')
    log(f'  Lines: {len(all_lines)}')

    print(f'\n{"="*60}', flush=True)
    print(f'Preview (first 25 lines)', flush=True)
    print(f'{"="*60}', flush=True)
    for i, line in enumerate(all_lines[:25]):
        print(f'  {line[:80]}', flush=True)
    if len(all_lines) > 25:
        print(f'  ... ({len(all_lines)} total lines)', flush=True)

    browser.close()
    p.stop()
    log('Complete')

if __name__ == '__main__':
    main()