#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信读书导出工具

用法:
    # 交互模式（默认）— 傻瓜式操作
    python weread_exporter.py

    # CLI 模式 — 供 AI Agent 调用
    python weread_exporter.py --list
    python weread_exporter.py --export <book_id>
    python weread_exporter.py --export <book_id> --trial y
    python weread_exporter.py --login

参数:
    --list                  列出书架书籍（JSON 格式）
    --export <book_id>      导出整本书，输出文件路径到 stdout
    --trial [y|n]           付费书策略: y=导出试读, n=跳过（默认自动）
    --login                 强制重新登录
"""

import sys, os, json, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ========== CLI 入口（有参数时走这里） ==========

def cli_main():
    parser = argparse.ArgumentParser(description='微信读书导出工具')
    parser.add_argument('--list', action='store_true', help='列出书架书籍（JSON格式）')
    parser.add_argument('--export', type=str, metavar='BOOK_ID', help='导出整本书')
    parser.add_argument('--skill', type=str, metavar='BOOK_ID',
                        help='Skill模式：按章节导出，显示导引树，每章一个文件')
    parser.add_argument('--range', type=str, metavar='N-M',
                        help='章节范围（配合 --skill 使用，如 "5-8"）')
    parser.add_argument('--api-id', type=str, metavar='API_BOOK_ID',
                        help='REST API 数字 bookId（配合 --skill 用于搜索来的书，书架书无需此参数）')
    parser.add_argument('--trial', choices=['y', 'n'], help='付费书策略: y=导出试读, n=跳过')
    parser.add_argument('--login', action='store_true', help='强制重新登录')
    parser.add_argument('--verbose', action='store_true', help='显示详细日志')

    args = parser.parse_args()

    # 延迟导入（CLI 才需要，交互模式不依赖）
    from weread_core import WeReadExporter

    exporter = WeReadExporter(headless=True, verbose=args.verbose)

    if args.login:
        if exporter.login():
            print('登录成功', flush=True)
        else:
            print('登录取消', flush=True)
            sys.exit(1)
        return

    if not exporter.check_login():
        print('未登录，请先运行以下命令进行登录:', flush=True)
        print('  python weread_exporter.py --login', flush=True)
        sys.exit(1)

    try:
        if args.list:
            books = exporter.list_books()
            print(json.dumps(books, ensure_ascii=False, indent=2), flush=True)
        elif args.export:
            filepath = exporter.export_book(args.export, trial=args.trial)
            if filepath:
                print(filepath, flush=True)
            else:
                print('导出取消', flush=True)
                sys.exit(1)
        elif args.skill:
            files = exporter.export_chapters(args.skill, chapter_range=args.range,
                                              trial=args.trial, api_book_id=args.api_id)
            if files:
                print('\n导出完成:', flush=True)
                for f in files:
                    print(f'  {f}', flush=True)
            else:
                print('导出取消', flush=True)
                sys.exit(1)
    finally:
        exporter.close()


# ========== 以下为交互模式原有代码（无参数时执行） ==========

import time, re
from collections import defaultdict
from playwright.sync_api import sync_playwright

COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookie.txt')
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
X_THRESHOLD = 600
CONSECUTIVE_EMPTY_LIMIT = 3
PAGE_SLEEP = 2


def log(msg):
    t = time.strftime('%H:%M:%S')
    print(f'[{t}] {msg}')


def load_cookie():
    if not os.path.exists(COOKIE_FILE):
        return None
    with open(COOKIE_FILE) as f:
        return f.read().strip()


def save_cookie(cookie_str):
    with open(COOKIE_FILE, 'w') as f:
        f.write(cookie_str)
    log(f'Cookie已保存 ({len(cookie_str)} 字符)')


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


def install_hook(page):
    page.evaluate(CANVAS_HOOK_JS)
    page.evaluate('window._capturedTexts = []')


def get_chapter_title(page):
    return page.evaluate("""() => {
        var el = document.querySelector('.renderTargetPageInfo_header_chapterTitle');
        return el ? el.textContent.trim() : '';
    }""")


def reassemble_page(texts):
    if not texts:
        return []
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
        left_items = [t for t in items if t['x'] < X_THRESHOLD]
        right_items = [t for t in items if t['x'] >= X_THRESHOLD]
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


def texts_identical(a, b):
    if len(a) != len(b):
        return False
    for t1, t2 in zip(a, b):
        if t1['text'] != t2['text'] or abs(t1['x'] - t2['x']) > 5:
            return False
    return True


def do_login():
    log('正在打开浏览器，请扫码登录微信读书...')
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False, args=['--disable-blink-features=AutomationControlled'])
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
                return !!document.querySelector('.readerTopBar_avatar, .nav_user_avatar, [class*="avatar"]');
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
            log('浏览器窗口已关闭')
            print()
            print('  [Y] 重新打开浏览器，扫码登录')
            print('  [N] 已退出，欢迎下次使用')
            choice = input('  请选择 (Y/N): ').strip().upper()
            if choice == 'Y':
                browser.close()
                p.stop()
                return do_login()
            else:
                break

    if login_ok:
        log('登录成功！')
        cookies = ctx.cookies()
        cookie_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies])
        save_cookie(cookie_str)
    else:
        log('已退出，欢迎下次使用')

    browser.close()
    p.stop()
    return login_ok


def ensure_login():
    cookie = load_cookie()
    if not cookie:
        log('未检测到登录信息，需要先登录')
        if not do_login():
            sys.exit(0)
        cookie = load_cookie()
    else:
        log('检测到登录状态')
    return cookie


def select_book(page):
    log('正在获取书架...')
    page.goto('https://weread.qq.com/web/shelf', wait_until='domcontentloaded', timeout=30000)
    time.sleep(3)

    books = page.evaluate("""() => {
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
            var titleEl = item.querySelector('[class*="title"], [class*="name"], .bookName, h3, h4');
            if (titleEl) title = titleEl.textContent.trim();
            if (!title) title = item.textContent.trim().split('\\n')[0].trim();
            results.push({title: title || '(无标题)', bookId: bookId});
        }
        return results;
    }""")

    prev_count = len(books)
    scroll_attempts = 0
    while scroll_attempts < 20:
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        time.sleep(1.5)
        books = page.evaluate("""() => {
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
                var titleEl = item.querySelector('[class*="title"], [class*="name"], .bookName, h3, h4');
                if (titleEl) title = titleEl.textContent.trim();
                if (!title) title = item.textContent.trim().split('\\n')[0].trim();
                results.push({title: title || '(无标题)', bookId: bookId});
            }
            return results;
        }""")
        if len(books) > prev_count:
            prev_count = len(books)
            scroll_attempts = 0
            log(f'  已加载 {len(books)} 本书...')
        else:
            scroll_attempts += 1
            if scroll_attempts >= 3:
                break
    log(f'书架加载完成: {len(books)} 本书')

    if not books:
        log('未找到书架书籍')
        return None, None

    PAGE_SIZE = 20
    total_pages = (len(books) + PAGE_SIZE - 1) // PAGE_SIZE
    current_page = 0

    while True:
        start = current_page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(books))
        page_books = books[start:end]

        print()
        print(f'  ─────────────────────────────────────')
        print(f'  你的书架 ({len(books)} 本)')
        print(f'  第{current_page+1}页/共{total_pages}页')
        print(f'  ─────────────────────────────────────')
        for i, book in enumerate(page_books, start=start+1):
            print(f'     [{i}] {book["title"]}')
        print(f'  ─────────────────────────────────────')
        print(f'  输入编号选书，按 Enter 翻页，输入 q 退出')
        print()

        choice = input(f'  请选择 (1-{len(books)} / Enter翻页 / q退出): ').strip().lower()
        if choice == 'q':
            log('已退出，欢迎下次使用')
            return None, None
        if choice == '':
            current_page = (current_page + 1) % total_pages
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(books):
                selected = books[idx]
                log(f'已选择: {selected["title"]}')
                return selected['bookId'], selected['title']
        except:
            pass
        print('  输入无效，请重新输入')


def export_full_book(page, book_id, book_title):
    log('打开阅读器...')
    page.goto(f'https://weread.qq.com/web/reader/{book_id}', wait_until='domcontentloaded', timeout=30000)
    time.sleep(8)

    page.evaluate("""() => {
        var reviewBtn = document.querySelector('.showBookReviews');
        if (reviewBtn && reviewBtn.classList.contains('showBookReviews_active')) {
            reviewBtn.click();
        }
        var toggle = document.querySelector('.isNormalReader');
        if (toggle) { toggle.click(); }
    }""")
    time.sleep(1)
    log('书友想法已关闭')

    install_hook(page)
    log('Canvas Hook已安装')

    need_pay = page.evaluate('() => !!document.querySelector(\'.need_pay_mask\')')
    if need_pay:
        print()
        print('  ╔══════════════════════════════════════╗')
        print('  ║  本书需要付费会员才能阅读完整内容    ║')
        print('  ╚══════════════════════════════════════╝')
        while True:
            choice = input('  是否导出试读部分？[Y/N]: ').strip().lower()
            if choice == 'y':
                log('用户选择导出试读部分')
                print()
                break
            elif choice == 'n':
                log('用户取消导出，返回书架')
                print()
                return None
            else:
                print('  请输入 Y 或 N')

    page.mouse.click(10, 10)
    time.sleep(0.5)
    page.click('.readerControls_item.catalog', timeout=5000)
    time.sleep(2)
    log('导航到首页: TOC[1] (版权信息)')
    page.locator('.readerCatalog_list_item').nth(1).click(timeout=5000)
    time.sleep(3)
    page.mouse.click(10, 10)
    time.sleep(1)

    safe_book = re.sub(r'[\\/:*?"<>|]', '_', book_title)
    out_dir = os.path.join(OUTPUT_DIR, safe_book)
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f'{safe_book}.md')

    log('开始导出...')
    print()
    print(f'  输出文件: {out_file}')
    print()

    all_lines = []
    total_pages = 0
    prev_raw = None
    same_count = 0
    empty_pages = 0

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
        page_lines = reassemble_page(first_raw)
        all_lines.extend(page_lines)
        all_lines.append('')
        log(f'  首页: {len(first_raw)} fillText calls')
    else:
        log('  首页捕获为空')

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
                print()
                print(f'  ⚠️  试读结束，本书剩余内容需付费会员可读')
                print(f'  当前已导出 {total_pages} 页（试读部分）')
                print(f'  如需完整内容，请开通微信读书会员后重试')
                print()
                log('停止导出（试读内容已保存）')
                break

            if not raw:
                empty_pages += 1
                if empty_pages >= 8:
                    log('全书完（连续8页无内容）')
                    break
                continue
            empty_pages = 0

            if prev_raw and texts_identical(raw, prev_raw):
                same_count += 1
                log(f'  检测到内容不变 ({same_count}/{CONSECUTIVE_EMPTY_LIMIT})')
                if same_count >= CONSECUTIVE_EMPTY_LIMIT:
                    log('全书完')
                    break
            else:
                same_count = 0

            if raw:
                page_lines = reassemble_page(raw)
                if page_lines:
                    all_lines.extend(page_lines)
                    all_lines.append('')
                prev_raw = raw
            else:
                prev_raw = None

            if page_num % 20 == 0:
                t = get_chapter_title(page)[:40] or '?'
                print(f'  [{time.strftime("%H:%M:%S")}] 进度: {total_pages} 页, 当前位置: "{t}"')

            if page_num % 50 == 0:
                with open(out_file, 'w', encoding='utf-8') as f:
                    f.write(f'# {book_title}\n\n')
                    f.write('\n'.join(all_lines))

        with open(out_file, 'w', encoding='utf-8') as f:
            f.write(f'# {book_title}\n\n')
            f.write('\n'.join(all_lines))

    except KeyboardInterrupt:
        print()
        log('用户中断导出')
        if all_lines:
            with open(out_file, 'w', encoding='utf-8') as f:
                f.write(f'# {book_title}\n\n')
                f.write('\n'.join(all_lines))
            print(f'  💾 已保存已导出的 {total_pages} 页内容至: {out_file}')
        print()
        return None

    file_size = os.path.getsize(out_file)
    print()
    print(f'  ✅ 导出完成！')
    print(f'  ─────────────────────────────────────')
    print(f'  文件: {out_file}')
    print(f'  大小: {file_size/1024:.1f} KB')
    print(f'  页数: {total_pages}')
    print(f'  行数: {len(all_lines)}')
    print(f'  ─────────────────────────────────────')

    return out_file


def main():
    # 检测是否有 CLI 参数
    if len(sys.argv) > 1:
        return cli_main()

    # 无参数 -> 显示帮助，引导使用 --login
    import argparse
    parser = argparse.ArgumentParser(description='微信读书导出工具')
    parser.add_argument('--list', action='store_true', help='列出书架书籍（JSON格式）')
    parser.add_argument('--export', type=str, metavar='BOOK_ID', help='导出整本书')
    parser.add_argument('--skill', type=str, metavar='BOOK_ID',
                        help='Skill模式：按章节导出，显示导引树，每章一个文件')
    parser.add_argument('--range', type=str, metavar='N-M',
                        help='章节范围（配合 --skill 使用，如 "5-8"）')
    parser.add_argument('--api-id', type=str, metavar='API_BOOK_ID',
                        help='REST API 数字 bookId（配合 --skill 用于搜索来的书）')
    parser.add_argument('--trial', choices=['y', 'n'], help='付费书策略: y=导出试读, n=跳过')
    parser.add_argument('--login', action='store_true', help='强制重新登录')
    parser.add_argument('--verbose', action='store_true', help='显示详细日志')
    parser.print_help()
    print()
    print('首次使用请先运行:  python weread_exporter.py --login')
    print()


if __name__ == '__main__':
    main()