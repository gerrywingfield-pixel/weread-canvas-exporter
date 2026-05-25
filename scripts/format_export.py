#!/usr/bin/env python3
"""
导出排版工具：去除非正文 + 按目录结构化

用法:
    python scripts/format_export.py <book_id> [--input <path>] [--output <path>]

功能:
    1. 用 API 获取目录结构
    2. 在导出文本中模糊匹配每个章节的起止位置
    3. 排除非正文（封面/版权/推荐序/后记/跋/附录等）
    4. 按 # → ## 层级添加 Markdown 标题
"""
import sys, os, re, json

# 排除规则：非作者所写的内容
EXCLUDE_PATTERNS = [
    '封面', '版权信息', '推荐语',
    '推荐序', '出版说明', '版本说明',
    '译后记', '后记', '跋', '附录',
    '参考文献', '目录',
    '作者简介', '内容简介',
]

# 保留规则：作者所写的内容
INCLUDE_PATTERNS = [
    '自序', '原序', '前言', '引言',
    '楔子', '献词', '题词',
]

def load_chapters(book_id: str) -> list:
    """从 API 获取目录"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.official_api import get_chapter_list
    return get_chapter_list(book_id)


def strip_ref(s: str) -> str:
    """去掉上标和引用标记"""
    # 去掉 ⁽¹⁵⁾ 格式的上标
    s = re.sub(r'⁽[⁰¹²³⁴⁵⁶⁷⁸⁹]+⁾', '', s)
    # 去掉 [15] 格式的引用
    s = re.sub(r'\[\d+\]', '', s)
    # 去掉 ¹⁵ 格式的纯上标数字
    s = re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹]+', '', s)
    return s.strip()


def strip_ws(s: str) -> str:
    """去掉所有空白类字符，包括全角空格 U+3000、零宽空格 U+200B 等"""
    s = re.sub(r'[\s\u3000\u2000-\u200F\u2028\u2029\uFEFF\u200B\u200C\u200D\u2060]+', '', s)
    return s


def fuzzy_find(content: str, title: str, min_line: int = -1) -> int:
    """
    模糊匹配标题在内容中的位置。
    Canvas Hook 会在换行处打断文本，标题可能被拆成多行。
    支持：连在一起的标题、跨行标题、带引用标记的标题。
    所有匹配候选统一评分，排除正文中的交叉引用。

    min_line: 结果的行号必须 **大于** 这个值（顺序约束）。
    """
    def is_line_start(s: str, pos: int) -> bool:
        return pos == 0 or s[pos - 1] == '\n'

    def score_pos(pos: int) -> int:
        """对匹配位置评分：行首+2，前一行空行+1"""
        if pos < 0:
            return -1
        s = 0
        if is_line_start(content, pos):
            s += 2
        before_newline = content.rfind('\n', 0, pos)
        if before_newline > 0:
            prev_line = content[content.rfind('\n', 0, before_newline) + 1:before_newline].strip()
            if not prev_line:
                s += 1
        return s

    def add_candidate(line_num: int, score: int):
        """添加候选，记录最好的"""
        nonlocal best
        if line_num <= min_line:
            return
        if best is None or (score, -line_num) > (best[0], -best[1]):
            best = (score, line_num)

    clean_title = strip_ref(title)
    candidates_text = [clean_title, title, strip_ws(clean_title)]

    best = None

    # 阶段 1：直接在 content 中搜索
    for candidate in candidates_text:
        start = 0
        while True:
            pos = content.find(candidate, start)
            if pos < 0:
                break
            line_num = content[:pos].count('\n')
            s = score_pos(pos)
            add_candidate(line_num, s)
            start = pos + 1

    # 阶段 2：去空白匹配（处理跨行标题）
    # 找到 stripped 版本中所有匹配位置，映射回原文件，评分
    stripped_title = strip_ws(strip_ref(title))
    stripped_content = strip_ws(content)

    start = 0
    while True:
        sp = stripped_content.find(stripped_title, start)
        if sp < 0:
            break
        # 映射回原文件位置
        orig_pos = 0
        si = 0
        while orig_pos < len(content):
            c = content[orig_pos]
            if not c.isspace() and c not in '\u3000\u200B\u200C\u200D\uFEFF\u2060':
                if si == sp:
                    break  # 找到了目标字符的原始位置
                si += 1
            orig_pos += 1
        line_num = content[:orig_pos].count('\n')
        s = score_pos(orig_pos)
        add_candidate(line_num, s)
        start = sp + 1

    if best is not None:
        return best[1]
    return -1


def is_excluded(title: str) -> bool:
    """判断是否应排除"""
    for pat in EXCLUDE_PATTERNS:
        if title.startswith(pat):
            return True
    for pat in INCLUDE_PATTERNS:
        if title.startswith(pat):
            return False
    # 默认排除：推荐序/后记等
    if '推荐序' in title:
        return True
    return False


def format_book(book_id: str, input_path: str = None, output_path: str = None, verify_only: bool = False):
    """主排版函数"""
    # 1. 获取目录
    chapters = load_chapters(book_id)
    if not chapters:
        print('❌ 无法获取目录', file=sys.stderr)
        return None

    # 2. 读取导出内容
    if not input_path:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_dir = os.path.join(base, 'output')
        # 用 API 获取书名（chapters[0] 是 '封面' 不是书名）
        try:
            from config.official_api import get_book_info
            info = get_book_info(book_id)
            book_title = info.get('title', '')
        except Exception:
            book_title = ''
        for d in os.listdir(out_dir):
            book_chars = book_title.replace(' ', '').lower()[:6]
            if book_chars and book_chars in d.replace(' ', '').lower():
                candidate = os.path.join(out_dir, d, f'{d}.md')
                if os.path.exists(candidate):
                    input_path = candidate
                    break
        if not input_path or not os.path.exists(input_path):
            print('❌ 找不到导出文件', file=sys.stderr)
            return None

    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')

    # ============================================================
    # 阶段 A：目录核验 — 先全文扫描每个标题，分类报告匹配情况
    # ============================================================
    print('=== 目录核验：API标题 vs 全文匹配 ===')
    matched = 0
    img_likely = 0  # 可能为图片标题
    danger_zones = []  # 级联删除风险区域

    last_line = -1
    for ch in chapters:
        title = ch.get('title', '')
        level = ch.get('level', '?')
        pos = fuzzy_find(content, title, min_line=last_line)
        if pos >= 0:
            matched += 1
            last_line = pos
            indent = '  ' * (int(level) - 1) if str(level).isdigit() else ''
            print(f'  ✅ [L{level}] {indent}{title}')
        else:
            # 尝试不加 min_line 约束再找一次（判断是否纯丢失）
            alt_pos = fuzzy_find(content, title, min_line=-1)
            if alt_pos >= 0:
                # 位置在前一个已匹配章节之前 → 交叉引用
                indent = '  ' * (int(level) - 1) if str(level).isdigit() else ''
                print(f'  ⚠️  [L{level}] {indent}{title}')
                print(f'     行{alt_pos+1}有文字匹配但被顺序约束过滤（可能是交叉引用而非标题）')
                l1 = int(level) if str(level).isdigit() else 1
                indent2 = '  ' * (l1 - 1)
                print(f'      → 级联删除风险：{indent2}{title} 前后内容可能被相邻章节吞并')
                danger_zones.append(title)
            else:
                img_likely += 1
                l1 = int(level) if str(level).isdigit() else 1
                indent = '  ' * (l1 - 1)
                print(f'  ❌ [L{level}] {indent}{title}')
                excluded = is_excluded(title)
                if excluded:
                    print(f'     (已排除，安全)')
                else:
                    print(f'     ⚠️ 正文章节！全文无匹配 → 可能是图片标题')
                    print(f'     → 级联删除风险：相邻的非正文章节可能吞掉本节内容')
                    danger_zones.append(title)

    total = len(chapters)
    print(f'  匹配成功: {matched}/{total}')
    print(f'  疑似图片标题: {img_likely}/{total}')
    if danger_zones:
        print(f'  ⚠️ 级联删除风险区域: {len(danger_zones)} 处')
        for z in danger_zones:
            print(f'    - {z}')
    else:
        print(f'  ✅ 无级联删除风险')
    print()

    if verify_only:
        return None

    # 3. 对每个章节，找到它在内容中的位置
    sections = []
    last_line = -1
    for ch in chapters:
        title = ch.get('title', '')
        level = ch.get('level', '1')
        pos = fuzzy_find(content, title, min_line=last_line)
        if pos >= 0:
            sections.append({
                'title': title,
                'level': level,
                'line_start': pos,
                'line_end': None,
            })
            last_line = pos
        else:
            sections.append({
                'title': title,
                'level': level,
                'line_start': -1,
                'line_end': None,
            })

    # 填充 line_end（下一个章节的行号 - 1）
    found_prev = None
    for s in sections:
        if s['line_start'] >= 0:
            if found_prev is not None:
                found_prev['line_end'] = s['line_start'] - 1
            found_prev = s
    # 最后一个章节结束于文件末尾
    if found_prev:
        found_prev['line_end'] = len(lines) - 1

    # 4. 构建排版后内容
    output_lines = []
    # 文件头元信息（用导出内容中的原始书名，或者 API 取的书名）
    first_line = lines[0].strip() if lines else ''
    if first_line.startswith('# '):
        doc_title = first_line[2:].strip()
    else:
        try:
            from config.official_api import get_book_info
            info = get_book_info(book_id)
            doc_title = info.get('title', chapters[0].get('title', '未命名书籍'))
        except Exception:
            doc_title = chapters[0].get('title', '未命名书籍')

    output_lines.append(f'# {doc_title}')
    output_lines.append('')

    kept_count = 0
    excluded_count = 0

    for s in sections:
        if s['line_start'] < 0 or s['line_end'] is None:
            continue

        title = s['title']
        level = int(s['level']) if str(s['level']).isdigit() else 1
        exclude = is_excluded(title)

        # 提取章节内容
        start_line = s['line_start']
        end_line = s['line_end']
        section_lines = lines[start_line:end_line + 1]

        # 过滤首尾空白行
        while section_lines and not section_lines[0].strip():
            section_lines.pop(0)
        while section_lines and not section_lines[-1].strip():
            section_lines.pop()

        if not section_lines:
            continue

        if exclude:
            excluded_count += 1
            continue

        # 加 Markdown 标题
        heading_mark = '#' * level
        output_lines.append(f'{heading_mark} {title}')
        output_lines.append('')

        # 加正文（跳过原文中残留的标题文本）
        # 标题可能因换行被拆成多行，构建一个宽松的匹配正则
        title_words = strip_ref(title).split()
        if not title_words:
            title_words = [title]
        title_pattern = r'\s*'.join(re.escape(w) for w in title_words)
        title_re = re.compile(title_pattern)
        section_text = '\n'.join(section_lines)
        # 用正则去掉原文中残留的标题
        cleaned = title_re.sub('', section_text, count=1).strip()
        if cleaned:
            output_lines.append(cleaned)
            output_lines.append('')
        kept_count += 1

    # 5. 输出
    result = '\n'.join(output_lines)

    if not output_path:
        dir_name = os.path.dirname(input_path)
        base_name = os.path.basename(input_path)
        name, ext = os.path.splitext(base_name)
        output_path = os.path.join(dir_name, f'{name}_排版版{ext}')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(result)

    total_chars = len(result)
    print(f'✅ 排版完成: {output_path}')
    print(f'   保留 {kept_count} 章, 排除 {excluded_count} 章')
    print(f'   共 {result.count(chr(10)) + 1} 行 / {total_chars:,} 字符')
    return output_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python scripts/format_export.py <book_id> [--input <path>] [--output <path>] [--verify]')
        sys.exit(1)

    book_id = sys.argv[1]
    input_path = None
    output_path = None
    verify_only = False

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == '--input' and i + 1 < len(sys.argv):
            input_path = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--output' and i + 1 < len(sys.argv):
            output_path = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--verify':
            verify_only = True
            i += 1
        else:
            i += 1

    format_book(book_id, input_path, output_path, verify_only)