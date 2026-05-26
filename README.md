# 微信读书导出工具

将微信读书电子书导出为 Markdown 格式，供 AI 分析和知识管理。

---

## 目录

- [不是什么 / 是什么](#不是什么--是什么)
- [完整工作流（推荐）](#完整工作流推荐)
- [快速开始（5分钟）](#快速开始5分钟)
- [CLI 命令参考](#cli-命令参考)
- [双ID系统：bookId vs readerId](#双id系统bookid-vs-readerid)
- [后处理排版](#后处理排版)
- [导出文件在哪里](#导出文件在哪里)
- [常见问题](#常见问题)
- [风险提示](#风险提示)
- [文件结构](#文件结构)

---

## 不是什么 / 是什么

**不是什么**：
- 不是爬虫——不破解、不绕过付费墙、不侵犯版权
- 不是阅读器——不取代微信读书 App 的阅读体验
- 不建议导漫画、绘本或纯图片书籍（Canvas Hook 抓不到图片里的文字）

**是什么**：
- 让 **AI 能"读"微信读书里的书**——输出结构化 Markdown，直接喂给 LLM
- 基于微信读书官方 Skill（[weread.qq.com/r/weread-skills](https://weread.qq.com/r/weread-skills)）二次开发，**具有官方 Skill 的全部功能**（搜索、书架、目录获取），叠加了逐页正文捕获和后处理排版能力
- 不破解付费：付费书只导出试读部分到付费墙为止
- **两步产出**：①全书导出为纯文本 → ②后处理排版（去除非正文 + 按 API 目录结构化）

> 💡 **推荐在 AI Agent 上使用**：本项目的 Skill 版本（Hermes Agent skill）提供完整的编排能力——搜索选书、版本确认、付费提示、后台导出、排版交付一条龙。纯 CLI 操作适合简单场景，用 Agent 编排才能发挥完整功能。

---

---

## 完整工作流（推荐）

```bash
# 1. 查书架 → 选书
python weread_exporter.py --list

# 2. 全书导出到纯文本（后台运行，自动通知完成）
python weread_exporter.py --export <readerId>

# 3. 目录核验（扫描 API 目录 vs 全文匹配，识别图片标题页和级联删除风险）
python scripts/format_export.py <bookId> --verify

# 4. 排版输出（去除非正文 + 按层级添加 Markdown 标题）
python scripts/format_export.py <bookId>
```

输出示例：
```
=== 目录核验：API标题 vs 全文匹配 ===
  ✅ [L1] 版权信息
  ✅ [L1] 推荐序
  ❌ [L1] 第一篇 筚路蓝缕 以启山林   ← 图片装饰页，安全
  ✅ [L2]   第一章 豆浆店谈出"芯"产业
  ✅ [L2]   第二章 56岁的创业者
  ...
  匹配成功: 19/25
  疑似图片标题: 6/25
  ✅ 无级联删除风险

✅ 排版完成: output/芯片浪潮：纳米工艺背后的全球竞争 - 佚名/芯片浪潮：纳米工艺背后的全球竞争_排版版.md
   保留 15 章, 排除 4 章
   共 11046 行 / 226KB
```

---

---

## 快速开始（5分钟）

### 环境要求

| 组件 | 要求 |
|------|------|
| 操作系统 | Windows Subsystem for Linux 2（WSL2），建议 Ubuntu 22.04+ |
| Python | 3.8+ |
| 浏览器 | Chromium（由 Playwright 自动安装，约 200MB） |

> ⚠️ 暂不支持 macOS / 原生 Windows，因为依赖 Chromium + WSLg 图形栈弹出登录窗口。

### 安装

```bash
# 1. 克隆项目
git clone https://github.com/gerrywingfield-pixel/weread-canvas-exporter.git
cd weread-canvas-exporter

# 2. 创建虚拟环境（推荐——避免污染系统 Python）
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖 + Chromium
pip install -r requirements.txt
python -m playwright install chromium

# 4. 验证安装
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print('✅ OK'); p.stop()"
```

或运行一键脚本：`./install.sh`

### 登录 + 获取 API Key

```bash
source venv/bin/activate      # 确保虚拟环境已激活
python weread_exporter.py --login
```

运行后会：
1. 弹出 Chromium 浏览器窗口
2. **微信扫码登录**
3. 登录成功后自动跳转到 **微信读书 Skill** 页面
4. 点击头像 → 「微信读书 Skill」 → **生成 API Key** → 粘贴到终端

> `--login` 一次扫码完成两件事：Cookie 写入 `cookie.txt` + API Key 写入 `config/.env`。以后不需要重新登录。

---

---

## CLI 命令参考

| 命令 | 说明 | 需要 API Key |
|:-----|:-----|:-----------:|
| `--login` | 扫码登录 + 引导获取 API Key | ❌ |
| `--list` | 列出书架书籍（JSON 格式） | ❌ |
| `--export <readerId>` | 整本导出（自动判定付费） | ❌ |
| `--export <readerId> --trial y` | 强制试读导出 | ❌ |
| `--export <readerId> --trial n` | 付费书跳过 | ❌ |
| `--verbose` | 显示详细翻页日志 | 随主命令 |

> 按章导出（`--skill`）已废弃，统一使用全书导出 + 后处理排版。

### 使用示例

```bash
# 登录
python weread_exporter.py --login

# 看书架上有哪些书
python weread_exporter.py --list

# 整本导出
python weread_exporter.py --export d6632d705cf769d6646fc55

# 后处理排版
python scripts/format_export.py 3300067765 --verify  # 先核验
python scripts/format_export.py 3300067765            # 再排版
```

---

---

## 双ID系统：bookId vs readerId

微信读书有两种 ID，容易混淆，搞错会导致导出失败。

| ID 类型 | 格式示例 | 用途 | 说明 |
|:--------|:---------|:-----|:-----|
| **bookId** | `3300199463` | 官方 REST API | 查书籍详情、作者、目录、付费状态 |
| **readerId** | `f4d32800813abb46ag013359` | 浏览器阅读器 URL | `--export` 导出时必需的 ID |

**一句话规则**：
- `--export` 参数 → **只接受 readerId**。`python weread_exporter.py --export 3300199463` 会 404 崩溃
- API 查作者/目录/付费 → **只接受 bookId**。`get_book_info(readerId)` 返回空

### 怎么拿到正确的 ID？

**书架书**（`--list`）：输出的就是 readerId，直接用于 `--export`。API 调用由代码自动匹配。

**搜索来的书**（不在书架）：搜索 API 返回的是 bookId。readerId 需要从浏览器搜索页获取——打开 `weread.qq.com` → 搜书名 → 点进结果 → 浏览器地址栏 URL 中的 `web/reader/` 后面就是 readerId。

> 在 AI Agent（Hermes）编排下，搜索→获取 readerId→导出是自动完成的。纯 CLI 用户需要手动从浏览器获取 readerId。

### 代码自动解析

`export_book()` 内部内置了 `_resolve_book_ids()`，传入 readerId 后自动：
1. 匹配书架 → 找到 bookId（书架书优先）
2. 用书名搜索 API → 补全 bookId、作者、付费类型（搜索书的兜底）
3. 从页面版权文本提取（最终兜底）

所以传给 `--export` 一个 readerId，代码会自动找到作者名和付费状态，输出目录正确命名为 `书名 - 作者名/`。

---

---

## 后处理排版

导出后的纯文本经过 `scripts/format_export.py` 加工，产出符合目录结构的 Markdown。

### 两步走

```bash
# 第①步：目录核验
python scripts/format_export.py <bookId> --verify

# 第②步：排版输出
python scripts/format_export.py <bookId>
```

### 功能

1. **目录核验** — 用 API `get_chapter_list()` 获取官方目录，逐条在全文搜索匹配，分类报告：
   - ✅ 正常匹配 → 走排版
   - ⚠️ 交叉引用过滤 → 检查顺序约束
   - ❌ 全文无匹配 → 图片标题页/装饰页（识别级联删除风险）

2. **排除非正文** — 自动剔除封面、版权信息、推荐序、附录等三方撰写内容；保留引言、自序、作者正文

3. **Markdown 结构化** — 按 `#`(L1) → `##`(L2) 层级编排

4. **级联删除保护** — 图片标题章节不会被相邻排除章节吞掉

---

---

## 导出文件在哪里

| 产出 | 路径 | 说明 |
|:-----|:-----|:-----|
| 全文导出 | `output/书名 - 作者名/书名 - 作者名.md` | 原始纯文本 |
| 排版版 | `output/书名 - 作者名/书名 - 作者名_排版版.md` | 结构化 Markdown |

复制到 Windows 桌面：
```bash
cp output/* /mnt/c/Users/你的用户名/Desktop/ -r
```

---

---

## 常见问题

### 需要先安装微信读书官方 Skill 吗？

**不需要。** 本工具已包含官方 API 的全部功能（同一网关、同一鉴权），用户无需安装官方 Skill。

### 导出到一半想停怎么办？

按 **Ctrl+C**，已导出的内容自动保存，不会丢失。

### 排版版和原始导出有什么区别？

| | 原始导出 | 排版版 |
|:---|:---------|:-------|
| 结构 | 纯平铺文本，无章节标题 | 按 `#` → `##` 层级组织 |
| 非正文（版权/推荐序） | 混在文件中 | 自动剔除 |
| 跨行标题 | 换行打断 | 用 API 目录重建 |
| AI 阅读 | 可以读，但无结构 | 可跳章节、按章摘要 |

### 导出的内容不全？

- 图片/表格内的文字无法捕获（Canvas Hook 的固有限制）
- 付费书只能导到试读部分
- 图片装饰标题页（纯 `drawImage`）无文字捕获，`--verify` 可识别

### Cookie 过期了？

程序自动检测。Cookie 保存在 `cookie.txt`，重新运行 `--login` 扫码一次即可。

### 翻页也太慢了吧？

每页间隔约 2 秒，模拟人类阅读速度。这是为了**降低触发微信读书风控的风险**。可后台运行：
```bash
nohup python weread_exporter.py --export <readerId> > export.log 2>&1 &
tail -f export.log
```

### `--export 3300199463` 报了 404 错误？

`--export` **只接受 readerId**（编码字符串格式），不接受数字 bookId。两者的区别见[双ID系统](#双id系统bookid-vs-readerid)章节。

书架书通过 `--list` 拿到的就是 readerId。搜索来的书需要从浏览器搜索页的阅读器 URL 中提取 readerId。

### WSL 关了 / 崩溃了怎么办？

```bash
# Windows PowerShell 中执行
wsl --shutdown
# 重新打开 WSL 终端
cd weread-canvas-exporter
source venv/bin/activate
# 继续用，cookie 还在
```

---

---

## 风险提示

### ⚠️ 版权说明

本工具仅供学习、研究和个人合理使用。导出内容仅限个人 AI 分析和知识管理，**请勿**：

- 将导出的内容公开发布、传播或商用
- 批量下载侵犯版权的内容
- 规避微信读书的付费会员机制

用户应自行承担使用本工具的法律责任。

### ⚠️ 账号安全

- 每页翻页间隔约 2 秒，模拟人类阅读速度
- 不要同时用多个终端操作同一账号
- 频繁、高速翻页可能触发微信读书风控，导致账号受限
- 导出完成后及时退出，不要长时间保持浏览器连接
- 使用虚拟环境运行，避免依赖冲突

### ⚠️ 数据安全

- `cookie.txt` 和 `config/.env` 包含登录凭据，**请勿分享给他人**或上传到 GitHub（已在 `.gitignore` 中排除）
- 导出内容为纯文本 Markdown，不含任何用户身份信息

### ⚠️ 技术局限性

- Canvas Hook 无法捕获图片/表格内的文字
- 全页装饰图片标题页（`drawImage` 渲染）无文字捕获，排版时 `--verify` 会标记
- WSL 环境长时间运行 Chromium 存在稳定性风险

---

---

## 文件结构

```
weread-canvas-exporter/
├── weread_exporter.py       # CLI 入口
├── weread_core.py           # 核心导出引擎（Python API）
├── config/
│   └── official_api.py      # 微信读书官方 REST API 封装
├── scripts/
│   └── format_export.py     # 后处理排版工具（核验 + 结构化）
├── install.sh               # 一键安装脚本
├── requirements.txt         # Python 依赖
├── AGENTS.md                # AI Agent 上下文说明
├── README.md                # 本文件
├── cookie.txt               # 登录 cookie（自动生成，排除在 Git 外）
├── config/.env              # API Key（自动生成，排除在 Git 外）
└── output/                  # 导出结果（排除在 Git 外）
```

---

---

*项目地址：https://github.com/gerrywingfield-pixel/weread-canvas-exporter*
*API框架来源：腾讯微信读书 Skill（https://weread.qq.com/r/weread-skills）—— 本工具基于官方 Skill 二次开发，在此致谢。*
*技术灵感来源：https://github.com/drunkdream/weread-exporter*