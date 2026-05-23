# 微信读书导出工具

将微信读书电子书导出为 Markdown 格式，供 AI 分析和知识管理。

---

## 目录

- [不是什么 / 是什么](#不是什么--是什么)
- [快速开始（5分钟）](#快速开始5分钟)
- [两种使用方式](#两种使用方式)
- [CLI 命令参考](#cli-命令参考)
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
- 让 **AI 能"读"微信读书里的书**——输出 Markdown 格式，直接喂给 LLM
- 基于微信读书官方 Skill（[weread.qq.com/r/weread-skills](https://weread.qq.com/r/weread-skills)）二次开发，**具有官方 Skill 的全部功能**（搜索、书架、目录获取），叠加了逐页正文捕获和按章导出能力
- 不破解付费：付费书只导出试读部分到付费墙为止

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

> **为什么要 API Key？** 按章节导出（`--skill`）需要它来获取官方目录结构，做章节边界判定。一键整本导出不需要 API Key。
>
> `--login` 一次扫码完成两件事：Cookie 写入 `cookie.txt` + API Key 写入 `config/.env`。以后不需要重新登录。

---
---

## 两种使用方式

### 方式一：整本导出（一键，不需要 API Key）

```bash
python weread_exporter.py --export <readerId>
```

适合场景：快速把整本书导出给 AI 分析，不在乎分不分章节。

**流程**：打开阅读器 → 检测付费 → ArrowRight 逐页翻 → 导出全书为一个 `.md` 文件。

### 方式二：按章导出（精细化，需要 API Key）

```bash
python weread_exporter.py --skill <readerId> --range 5-8 --api-id <数字bookId>
```

适合场景：只想导正文（跳过推荐序、版权页等非原著内容），或只导指定的某几章。

**流程**：
1. 展示导引树（列出所有一级标题）
2. 输入要导出的章节范围
3. 每章输出一个独立 `.md` 文件

### 如何获取 readerId 和 api_book_id？

| 来源 | readerId（阅读器URL中的ID） | api_book_id（API数字ID） |
|:-----|:--------------------------|:------------------------|
| 书架上的书 | `--list` 直接拿到 | `--list` 直接拿到 |
| 搜索来的书 | 浏览器搜索后点进去拿 | 搜索时自动拿到 |

**书架书**按章导出最简单：
```bash
# 1. 列出书架
python weread_exporter.py --list

# 2. 直接导出（书架书的 api_book_id 自动获取）
python weread_exporter.py --skill <readerId> --range 5-8
```

**搜索来的书**（不在书架上的）需要显式传 `--api-id`：
```bash
python weread_exporter.py --skill <readerId> --range 5-8 --api-id <数字bookId>
```

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
| `--skill <readerId> --range N-M` | 按章导出（书架书） | ✅ |
| `--skill <readerId> --range N-M --api-id <id>` | 按章导出（搜索来的书） | ✅ |
| `--verbose` | 显示详细翻页日志 | 随主命令 |

### 使用示例

```bash
# 登录
python weread_exporter.py --login

# 看书架上有哪些书
python weread_exporter.py --list

# 整本导出
python weread_exporter.py --export d6632d705cf769d6646fc55

# 按章导出第5-8章（书架书）
python weread_exporter.py --skill d6632d705cf769d6646fc55 --range 5-8

# 按章导出一章（搜索来的书）
python weread_exporter.py --skill 48d32cc0813ab80f8g015eec --range 5-5 --api-id 3300067765
```

---
---

## 导出文件在哪里

| 导出方式 | 输出路径 |
|:---------|:---------|
| 整本导出 | `output/书名/书名.md` |
| 按章导出 | `output/书名/第五章 标题.md` |
| 付费书试读 | `output/书名/书名（试读部分）.md` |

复制到 Windows 桌面：
```bash
cp output/* /mnt/c/Users/你的用户名/Desktop/ -r
```

---

---

## 多 Agent 能力

本工具理论上支持多 Agent 并行协作，让 AI 代理独立工作，互不干扰。典型场景：

| 场景 | 说明 |
|:-----|:------|
| **多 Agent 导不同书** | 每个 Agent 登录不同微信读书账号，各自导出不同的书，互不影响 |
| **多 Agent 导同一本书** | 拆分为多个章节范围，分配给不同 Agent 并行导出，大幅缩短单本书的导出耗时 |

技术上可行，但需要注意：
- 每个 Agent 需使用**独立的微信读书账号**（同一账号多端操作可能触发风控）
- 每个 Agent 有自己的 Cookie 和 API Key，互不干扰
- 导出完成后由下游调度 Agent 合并结果

> 欢迎社区在这方面继续探索。如果你实现了更好的多 Agent 编排方案，欢迎提交 Issue 或 PR。

---
---

## 常见问题

### 需要先安装微信读书官方 Skill 吗？

**不需要。** 本工具已包含官方 API 的全部功能（同一网关、同一鉴权），用户无需安装官方 Skill。

### 导出到一半想停怎么办？

按 **Ctrl+C**，已导出的内容自动保存，不会丢失。

### 导出的内容不全？

- 图片/表格内的文字无法捕获（Canvas Hook 的固有限制）
- 付费书只能导到试读部分
- 如果使用按章导出，检查 `--range` 参数是否正确——导引树中的编号可能比直觉少（扉页等不计编号）

### Cookie 过期了？

程序自动检测。Cookie 保存在 `cookie.txt`，重新运行 `--login` 扫码一次即可。

### 翻页也太慢了吧？

每页间隔约 2 秒，模拟人类阅读速度。这是为了**降低触发微信读书风控的风险**。可后台运行：
```bash
nohup python weread_exporter.py --export <readerId> > export.log 2>&1 &
tail -f export.log
```

### WSL 关了 / 崩溃了怎么办？

```bash
# Windows PowerShell 中执行
wsl --shutdown
# 重新打开 WSL 终端
cd weread-canvas-exporter
source venv/bin/activate
# 继续用，cookie 还在
```

### 可以同时导多本书吗？

**同一账号不建议**——多终端同步翻页可能触发风控。不同账号各自一个终端安全。

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
- WSL 环境长时间运行 Chromium 存在稳定性风险
- 导出速度受微信读书页面渲染速度和翻页间隔限制，无法加速

---
---

## 文件结构

```
weread-canvas-exporter/
├── weread_exporter.py       # CLI 入口
├── weread_core.py           # 核心导出引擎（Python API）
├── config/
│   └── official_api.py      # 微信读书官方 REST API 封装
├── install.sh               # 一键安装脚本
├── requirements.txt         # Python 依赖
├── AGENTS.md                # AI Agent 上下文说明
├── README.md                # 本文件
├── cookie.txt               # 登录 cookie（自动生成，排除在 Git 外）
├── config/.env              # API Key（自动生成，排除在 Git 外）
└── output/                  # 导出结果（排除在 Git 外）
```

---

*项目地址：https://github.com/gerrywingfield-pixel/weread-canvas-exporter*
*API框架来源：腾讯微信读书 Skill（https://weread.qq.com/r/weread-skills）—— 本工具基于官方 Skill 二次开发，在此致谢。*
*技术灵感来源：https://github.com/drunkdream/weread-exporter*