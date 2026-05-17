# weread-exporter

微信读书书籍导出工具。输出 Markdown 格式文本，供 AI 分析使用。

## CLI 命令

```bash
# 首次使用需要先登录（交互式）
python weread_exporter.py --login

# 列出书架（JSON 格式）
python weread_exporter.py --list

# 导出整本书（App 模式）
python weread_exporter.py --export <book_id>

# Skill 模式：按章节导出（显示导引树，每章一个文件）
python weread_exporter.py --skill <book_id>

# Skill 模式 + 指定章节范围
python weread_exporter.py --skill <book_id> --range 5-8

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
files = exporter.export_chapters(books[0]['id'])
# files = ['output/书名/第一章.md', 'output/书名/第二章.md', ...]

# Skill 模式 + 指定章节范围
files = exporter.export_chapters(book_id, chapter_range='5-8')

# 付费书策略
filepath = exporter.export_book(book_id, trial='y')  # 导试读

exporter.close()
```

## 输出格式

- 文件: `output/书名/书名.md`
- 编码: UTF-8
- 文本按阅读顺序重组（左栏→右栏），无 `【左栏】【右栏】` 标记
- 图片/表格内的文字无法捕获

## 注意事项

- 首次使用需要扫码登录（弹出浏览器）
- cookie 保存在 `cookie.txt`，请勿泄露
- 导出速度约 2 秒/页
- 同一账号同时只导一本书，多账号可并行
- 长时间运行可能触发 WeRead 风控，建议控制导出频率
- WSL 环境长时间运行有崩溃风险，建议每 50 页检查输出文件
- 付费书导到试读墙自动停止，不会中途打断

## 文件结构

```
weread-canvas-exporter/
├── weread_exporter.py     # CLI 入口
├── weread_core.py         # Python API 核心
├── auth.py                # 认证模块
├── install.sh             # 一键安装
├── README.md              # 使用说明
├── cookie.txt             # 登录 cookie（自动生成）
└── output/                # 导出文件目录
```