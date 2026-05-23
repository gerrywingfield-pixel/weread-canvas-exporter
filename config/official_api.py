#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeRead Official REST API 封装
统一入口: POST https://i.weread.qq.com/api/agent/gateway
鉴权: Authorization: Bearer $WEREAD_API_KEY
参考: ~/.hermes/skills/automation/weread-exporter/references/official-weread-skill-vs-exporter.md
      官方 Skill: /tmp/weread-skills-extracted/weread-skills/SKILL.md
"""
import os, json, requests

API_GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.3"

def _load_key():
    key = os.environ.get("WEREAD_API_KEY", "")
    if not key:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("WEREAD_API_KEY="):
                        key = line.strip().split("=", 1)[1]
    if not key:
        raise RuntimeError("WEREAD_API_KEY 未设置。请在 config/.env 或环境变量中配置。")
    return key

def _post(api_name: str, **params) -> dict:
    key = _load_key()
    body = {"api_name": api_name, "skill_version": SKILL_VERSION, **params}
    r = requests.post(API_GATEWAY, json=body,
                       headers={"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"},
                       timeout=15)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("errcode", 0) != 0:
        raise RuntimeError(f"[{api_name}] errcode={data.get('errcode')}: {data.get('errmsg', 'unknown')}")
    return data

# ─── 1. 书架 ───
def get_shelf() -> dict:
    """获取个人书架（含电子书、有声书、文章收藏）"""
    return _post("/shelf/sync")

def get_shelf_full() -> list:
    """
    获取书架**全量**电子书列表（合并分段结果）。
    /shelf/sync 单次返回通常不全，此函数翻页直到没有新书。
    返回: [{"bookId":, "title":, "author":, ...}, ...]
    """
    seq = 0
    seen_ids = set()
    all_books = []
    while True:
        data = _post("/shelf/sync")
        books = data.get("books", [])
        new_count = 0
        for b in books:
            bid = b.get("bookId")
            if bid and bid not in seen_ids:
                seen_ids.add(bid)
                all_books.append(b)
                new_count += 1
        if new_count == 0:
            break
        seq += 1
        if seq >= 20:
            break
    return all_books

# ─── 2. 搜索 ───
def search(keyword: str, count: int = 10) -> dict:
    """在微信读书书城搜索书籍"""
    return _post("/store/search", keyword=keyword, count=count)

def search_full(keyword: str, author: str = None, max_discover: int = 50, top_n: int = 15, min_rating: int = None) -> dict:
    """
    多层次搜索补全：单次搜索可能被限流（作者名只返回 3/13 条），
    此函数用多维度关联搜索 + 交叉去重 + 作者校验补全结果。
    
    参数:
        keyword: 初始搜索词（作者名、书名片段等）
        author: 期望校验的作者名（可选）。指定后只保留该作者书籍
        max_discover: 最大搜索次数上限
    
    返回:
        dict: {'total': 总结果数, 'top_n': 实际显示数, 'has_more': 是否还有更多,
               'books': [按评分降序的 top_n 本]}
    """
    from collections import Counter
    
    def collapse(r):
        """从 search() 的结果中提取标准化书籍列表"""
        books = []
        for res in r.get("results", []):
            for bk in res.get("books", []):
                bi = bk.get("bookInfo") or {}
                bid = bi.get("bookId")
                if bid:
                    books.append({
                        "bookId": bid,
                        "title": bi.get("title", ""),
                        "author": bi.get("author", ""),
                        "rating": bi.get("newRating", 0),
                        "cover": bi.get("cover", ""),
                        "payType": bi.get("payType", 0),
                        "group": res.get("title", ""),
                    })
        return books
    
    def known_phrases(books):
        """从已有书名的 title 中提取搜索特征短语"""
        phrases = set()
        for b in books:
            t = b.get("title", "")
            # 去掉序号前缀后的核心语义段
            parts = [p.strip() for p in t.split("：") if p.strip()]
            for p in parts:
                if len(p) >= 4:
                    phrases.add(p)
        return list(phrases)[:5]  # 最多 5 个特征短语
    
    seen = set()
    all_books = []
    
    # Phase 1: 初始搜索
    r = search(keyword, count=50)
    books = collapse(r)
    for b in books:
        if b["bookId"] not in seen:
            seen.add(b["bookId"])
            all_books.append(b)
    
    attempts = 0
    # Phase 2: 用已知书名的特征短语补搜
    extra_phrases = known_phrases(books)
    for phrase in extra_phrases:
        if attempts >= max_discover:
            break
        attempts += 1
        try:
            r2 = search(phrase, count=30)
            candidates = collapse(r2)
            for bc in candidates:
                if bc["bookId"] not in seen:
                    seen.add(bc["bookId"])
                    all_books.append(bc)
        except Exception:
            continue
    
    # Phase 3: 如果是作者搜索且结果偏少，尝试常见行业词补搜
    industry_words = ["财务", "企业", "会计", "管理", "经济", "投资", "金融"]
    total_before = len(all_books)
    for word in industry_words[:4]:
        if attempts >= max_discover:
            break
        attempts += 1
        try:
            r3 = search(f"{keyword} {word}", count=15)
            candidates = collapse(r3)
            for bc in candidates:
                if bc["bookId"] not in seen:
                    seen.add(bc["bookId"])
                    all_books.append(bc)
        except Exception:
            continue
    
    # 过滤：如果指定了 author，用 /book/info 精确验证
    if author:
        verified = []
        for b in all_books:
            try:
                info = _post("/book/info", bookId=b["bookId"])
                real_author = info.get("author", "")
                if author in real_author:
                    b["author"] = real_author
                    verified.append(b)
            except Exception:
                continue
        all_books = verified

    # 按评分降序
    all_books.sort(key=lambda x: -x.get("rating", 0))

    total = len(all_books)
    # 评分过滤
    if min_rating is not None:
        all_books = [b for b in all_books if b.get("rating", 0) >= min_rating]

    has_more = len(all_books) > top_n if top_n else False
    display = all_books[:top_n] if top_n else all_books

    return {"total": total, "top_n": min(top_n, len(display)) if top_n else len(display), "has_more": has_more, "books": display}

# ─── 3. 书籍详情 ───
def get_book_info(book_id: str) -> dict:
    """获取书籍基本信息（标题/作者/字数/评分等）"""
    return _post("/book/info", bookId=book_id)

def get_chapter_list(book_id: str) -> list:
    """获取章节目录（含 chapterUid/chapterIdx/title/level/wordCount 等）"""
    data = _post("/book/chapterinfo", bookId=book_id)
    return data.get("chapters", [])

# ─── 4. 阅读统计 ───
def get_read_stats(mode: str = "overall") -> dict:
    """阅读统计详情。mode: weekly/monthly/annually/overall"""
    return _post("/readdata/detail", mode=mode)

# ─── 5. 笔记划线（热门） ───
def get_best_bookmarks(book_id: str, count: int = 10) -> dict:
    """获取书籍热门划线（集体热门高亮）"""
    return _post("/book/bestbookmarks", bookId=book_id, count=count)

# ─── 6. 推荐 ───
def get_recommend(book_id: str, count: int = 5) -> dict:
    """基于某本书的推荐"""
    return _post("/book/recommend", bookId=book_id, count=count)

def get_similar(book_id: str, count: int = 5) -> dict:
    """相似书籍推荐"""
    return _post("/book/similar", bookId=book_id, count=count)

# ─── CLI 自测入口 ───
if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "shelf"
    try:
        if cmd == "shelf":
            d = get_shelf()
            books = d.get("books", [])
            print(f"书架(单页): {len(books)} 本电子书")
            for b in books[:5]:
                print(f"  [{b['bookId']}] {b['title']}  作者: {b.get('author','?')}")
        elif cmd == "shelf-full":
            books = get_shelf_full()
            print(f"书架(全量): {len(books)} 本电子书")
            for i, b in enumerate(books, 1):
                print(f"  {i:2d}. [{b['bookId']}] {b['title']}  作者: {b.get('author','?')}")
        elif cmd == "search":
            kw = sys.argv[2] if len(sys.argv) > 2 else "三体"
            d = search(kw)
            books = d.get("results", [{}])[0].get("books", [])
            print(f"搜索「{kw}」: {len(books)} 条")
            for b in books[:5]:
                info = b.get("bookInfo", {})
                print(f"  [{info.get('bookId','?')}] {info.get('title','?')}  ⭐{info.get('newRating','?')}")
        elif cmd == "chapters":
            bid = sys.argv[2] if len(sys.argv) > 2 else "3300024052"
            d = get_chapter_list(bid)
            levels = {}
            for c in d:
                lv = c.get("level", 0)
                levels[lv] = levels.get(lv, 0) + 1
            print(f"bookId={bid}  章节数={len(d)}  level分布: {dict(sorted(levels.items()))}")
            for c in d[:10]:
                print(f"  [L{c.get('level','?')}] {c.get('title','?')}")
        elif cmd == "search-full":
            kw = sys.argv[2] if len(sys.argv) > 2 else "财务"
            # 可选第三个参数作为 top_n
            top_n = int(sys.argv[3]) if len(sys.argv) > 3 else 15
            r = search_full(kw, top_n=top_n)
            total = r["total"]
            showing = len(r["books"])
            print(f"搜索「{kw}」: 共 {total} 本相关  显示前 {showing} 本（按评分降序）")
            if r["has_more"]:
                print(f"  ⚠ 还有 {total - showing} 本未显示，用 --search-full <keyword> <N> 查看更多")
            for i, b in enumerate(r["books"], 1):
                rating = b.get("rating", 0)
                star = "⭐" if rating >= 800 else "☆" if rating >= 500 else "·"
                print(f"  {i:2d}. {star} {b['title']} ({b.get('author','?')})")
        elif cmd == "stats":
            d = get_read_stats()
            rs = {s["stat"]: s["counts"] for s in d.get("readStat", [])}
            t = d.get("totalReadTime", 0)
            h, m = divmod(t, 3600)
            print(f"总计: {h}h{m//60}m  读过: {rs.get('读过','?')}  读完: {rs.get('读完','?')}  天数: {d.get('readDays','?')}")
        else:
            print(f"未知命令: {cmd}")
            print("可用: shelf | search <keyword> | chapters [bookId] | stats")
    except Exception as e:
        print(f"错误: {e}")
