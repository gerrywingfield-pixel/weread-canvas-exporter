# weread-exporter

微信读书书籍导出工具。输出结构化 Markdown，供 AI 分析使用。

## 完整工作流（推荐）

```bash
# 1. 登录（扫码 + 获取 API Key 一步完成）
python weread_exporter.py --login

# 2. 看书架
python weread_exporter.py --list

# 3. 全书导出（后台运行）
python weread_exporter.py --export <readerId>

# 4. 目录核验
python scripts/format_export.py <bookId> --verify

# 5. 排版输出（去除非正文 + 按目录结构化）
python scripts/format_export.py <bookId>
```

## CLI 命令

```bash
# 登录
python weread_exporter.py --login

# 列出书架
python weread_exporter.py --list

# 全书导出（自动判定付费）
python weread_exporter.py --export <readerId>

# 强制试读导出
python weread_exporter.py --export <readerId> --trial y

# 详细日志
python weread_exporter.py --export <readerId> --verbose
```

> `--skill`（按章导出）已废弃，统一使用全书导出 + 后处理排版。

## Python API

```python
from weread_core import WeReadExporter

exporter = WeReadExporter(verbose=True)
if not exporter.check_login():
    exporter.login()

# 获取书架
books = exporter.list_books()
# books = [{'id': 'xxx', 'title': '书名'}, ...]

# 导出整本书
filepath = exporter.export_book(books[0]['id'])
# filepath = 'output/书名/书名.md'

exporter.close()
```

## 后处理排版

```bash
# 目录核验（识别图片标题页和级联删除风险）
python scripts/format_export.py <bookId> --verify

# 排版输出（排除非正文 + 按 # → ## 层级编排）
python scripts/format_export.py <bookId>
```

输出：`output/书名 - 作者名/书名 - 作者名_排版版.md`

## 输出格式

- 原始导出: `output/书名 - 作者名/书名 - 作者名.md`（纯文本平铺）
- 排版版: `output/书名 - 作者名/书名 - 作者名_排版版.md`（结构化 Markdown）
- 编码: UTF-8
- 图片/表格内的文字无法捕获
- 图片装饰标题页无文字，`--verify` 可识别

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
├── scripts/
│   └── format_export.py   # 后处理排版工具
├── install.sh             # 一键安装
├── requirements.txt       # Python 依赖
├── AGENTS.md              # AI Agent 上下文说明
├── README.md              # 使用说明
├── cookie.txt             # 登录 cookie（自动生成）
└── output/                # 导出文件目录
```