# weread-exporter

微信读书书籍导出工具。输出 Markdown 格式文本，供 AI 分析使用。

## CLI 命令

```bash
# 登录（一步完成：扫码 + 引导获取 API Key）
python weread_exporter.py --login

# 列出书架（JSON 格式）
python weread_exporter.py --list

# 导出整本书（自动判定付费）
python weread_exporter.py --export <book_id>

# Skill 模式：按章节导出（显示导引树，每章一个文件）
#   书架书：--skill <readerId> --range N-M
#   搜索来的书需额外传 --api-id <数字bookId>
python weread_exporter.py --skill <readerId> --range 5-8
python weread_exporter.py --skill <readerId> --range 5-8 --api-id <bookId>

# 付费书只导出试读部分
python weread_exporter.py --export <book_id> --trial y
python weread_exporter.py --skill <book_id> --trial y

# 详细日志模式
python weread_exporter.py --export <book_id> --verbose
```

## Python API

```python
from weread_core import WeReadExporter

exporter = WeReadExporter(verbose=True)
if not exporter.check_login():
    exporter.login()

# 获取书架
books = exporter.list_books()
# books = [{'id': 'xxx', 'title': '书名'}, ...]

# App 模式：导出整本书
filepath = exporter.export_book(books[0]['id'])
# filepath = 'output/书名/书名.md'

# Skill 模式：按章节导出（每个一级标题一个文件）
files = exporter.export_chapters(books[0]['id'], chapter_range='5-8')
# files = ['output/书名/第五卷.md', ...]

exporter.close()
```

## 输出格式

- 文件: `output/书名/书名.md`（整本）或 `output/书名/第X卷.md`（按章）
- 编码: UTF-8
- 文本按阅读顺序重组（左栏→右栏）
- 图片/表格内的文字无法捕获

## 注意事项

- 首次使用 `--login` 扫码登录 + 获取 API Key
- cookie 保存在 `cookie.txt`，API Key 保存在 `config/.env`，请勿泄露
- 导出速度约 2 秒/页
- 同一账号同时只导一本书，多账号可并行
- 长时间运行可能触发 WeRead 风控，建议控制导出频率
- 付费书导到试读墙自动停止

## 文件结构

```
weread-canvas-exporter/
├── weread_exporter.py     # CLI 入口
├── weread_core.py         # Python API 核心
├── config/
│   └── official_api.py    # 官方 REST API 封装
├── install.sh             # 一键安装
├── requirements.txt       # Python 依赖
├── README.md              # 使用说明
├── cookie.txt             # 登录 cookie（自动生成）
└── output/                # 导出文件目录
```