# LinuxdoScanner

一个面向 `linux.do` 的新主题监控器，目标是：

- 首次启动默认抓取最新 30 条主题
- 后续默认每 5 分钟做一次增量抓取
- 先尝试直接调用 Discourse JSON API
- 默认优先复用当前用户 Chrome/Edge 的浏览器会话与 Cookie
- 支持通过 Chrome 扩展直接使用“当前已登录浏览器”抓取，无需再开镜像浏览器
- 如果遇到 Cloudflare `403`，自动切到浏览器上下文抓取同一个 JSON 接口
- 将结果落到 SQLite
- 对 AI 羊毛、key/额度、公益站等内容做标注
- 命中高优先级时支持邮件通知

## 当前实现

- 已支持 `auth`、`run-once`、`poll`、`probe` 四个命令
- 已支持 `bridge-server`、`bridge-info`，可配合 Chrome 扩展让当前已登录浏览器直接推送主题数据到本地服务
- 已验证：
  - `site.json` 原始 HTTP 可访问
  - `latest.json` 原始 HTTP 在当前网络下会被 Cloudflare 拦截
  - 程序现在会优先尝试读取当前用户浏览器的 `linux.do` Cookie
  - 浏览器 fallback 可以抓到公开主题列表
  - 公开主题详情、SQLite 入库、增量游标、规则识别都已跑通

## 安装

```bash
python -m pip install -e .
```

## 当前浏览器优先

如果你希望直接使用“当前这个已经登录过 linux.do 的 Chrome 浏览器”，推荐优先使用扩展桥接方案，而不是 CDP/镜像浏览器。

### 推荐方案：Chrome 扩展桥接当前浏览器

1. 在终端启动本地接收服务：

```bash
python main.py bridge-server
```

2. 查看扩展目录和本地服务地址：

```bash
python main.py bridge-info
```

默认会输出：

- `bridge_url: http://127.0.0.1:8765`
- `extension_dir: <项目根目录>/chrome-extension`

3. 在 Chrome 打开：

- `chrome://extensions/`
- 打开右上角“开发者模式”
- 选择“加载已解压的扩展程序”
- 选择项目里的 `chrome-extension` 目录

4. 打开扩展弹窗，确认：

- 本地服务地址是 `http://127.0.0.1:8765`
- 如果你设置了 `LINUXDO_BRIDGE_TOKEN`，就在扩展里填同一个 token
- 勾选“启用自动同步”
- 轮询间隔默认 5 分钟

5. 点击扩展里的“立即同步”

扩展会直接在你当前登录的浏览器里：

- 检查 `linux.do` 登录 Cookie
- 拉取最新主题与详情 JSON
- 将数据 POST 到本地 `bridge-server`
- 复用现有 SQLite、AI 标注、邮件通知逻辑

如果你设置了：

```bash
set LINUXDO_REQUIRE_LOGIN=1
```

那么当前浏览器未登录 `linux.do` 时，扩展同步会直接报错，不会只抓公开主题。

### 备选方案：调试浏览器 / 镜像目录

程序也保留了原来的 CDP/调试浏览器方案：

- 自动探测最近使用的浏览器 profile
- 直接读取 `linux.do` 相关 Cookie
- 能复用就直接复用，不再默认要求单独登录一次

如果你想继续使用这条方案，可以额外开启浏览器 DevTools 远程调试，再配置：

```bash
set LINUXDO_BROWSER_CDP_URL=http://127.0.0.1:9222
```

这适合你明确希望抓取过程直接走当前浏览器上下文时使用。

注意：

- 从 Chrome 136 开始，`--remote-debugging-port` 对默认真实用户目录会被限制
- 因此本项目现在会建议你先复制当前 profile 到受控镜像目录，再从镜像目录启动调试浏览器
- `python main.py start-debug-browser` 默认会完整镜像整个浏览器用户目录，并输出复制进度
- 但在新版 Chrome/Windows 上，`linux.do` 这类站点的登录 Cookie 可能不会随着镜像直接保留下来
- 所以“安全且稳定”的推荐路径是：镜像启动调试浏览器后，在同一个调试浏览器里执行一次 `python main.py auth` 保存可用会话
- 不建议把真实浏览器用户目录直接做软链接 / junction 后拿去远程调试，这样有污染真实 profile 的风险
- 一旦程序连上已登录的调试浏览器，会自动把当前登录会话保存到 `data/browser/storage_state.json`

## 首次登录 / 兜底会话

`python main.py auth` 现在默认会尝试复用你当前用户正在使用的 Chrome/Edge profile，而不是打开一个全新的隔离 profile。

如果当前浏览器正在运行且 profile 被锁住，程序会直接提示你：

- 先关闭浏览器后再执行 `python main.py auth`
- 或者开启浏览器远程调试并设置 `LINUXDO_BROWSER_CDP_URL`

如果你就是想手工开一个隔离浏览器再登录，也可以显式使用：

```bash
python main.py auth --isolated
```

如果你希望复用当前登录状态并开启可连接的调试浏览器，推荐直接使用：

```bash
python main.py start-debug-browser
$env:LINUXDO_BROWSER_CDP_URL="http://127.0.0.1:9222"
python main.py auth
python main.py poll
```

默认登录命令：

```bash
python main.py auth
```

如果已经配置了 `LINUXDO_BROWSER_CDP_URL`，`python main.py auth` 不会新开独立浏览器，而是直接附着到当前调试浏览器并新开标签页。

运行后会在那个浏览器里完成：

1. Cloudflare 验证
2. Linux.do 登录

检测到登录完成后，会自动保存到：

- `data/browser/storage_state.json`
- `data/browser/session_meta.json`

## 试运行

检查当前环境对 `linux.do` 的访问情况：

```bash
python main.py probe
```

执行一次抓取：

```bash
python main.py run-once
```

持续轮询：

```bash
python main.py poll
```

## 数据文件

- SQLite: `data/linuxdo.sqlite3`
- 浏览器会话: `data/browser/`

当前主要表：

- `topics`: 主题数据、正文摘要、AI 标注、通知状态
- `crawler_state`: 增量游标，例如 `last_seen_topic_id`

## 重要环境变量

- `LINUXDO_POLL_INTERVAL_SECONDS`
  默认 `300`
- `LINUXDO_BOOTSTRAP_LIMIT`
  默认 `30`
- `LINUXDO_MAX_PAGES_PER_RUN`
  默认 `10`
- `LINUXDO_BROWSER_EXECUTABLE`
  浏览器路径，不填时会自动尝试本机 Chrome/Edge
- `LINUXDO_BROWSER_COOKIE_SOURCE`
  `auto`、`chrome`、`edge`、`off`，默认 `auto`
- `LINUXDO_BROWSER_PROFILE`
  指定浏览器 profile 名称，例如 `Profile 1`
- `LINUXDO_BROWSER_CDP_URL`
  连接当前正在运行浏览器的 DevTools 地址，例如 `http://127.0.0.1:9222`
- `LINUXDO_REQUIRE_LOGIN`
  设为 `1` 后，如果当前浏览器未登录 `linux.do`，程序会直接报错，而不是只抓公开主题
- `LINUXDO_BRIDGE_HOST`
  本地扩展桥接服务监听地址，默认 `127.0.0.1`
- `LINUXDO_BRIDGE_PORT`
  本地扩展桥接服务端口，默认 `8765`
- `LINUXDO_BRIDGE_TOKEN`
  可选。设置后，Chrome 扩展向本地服务推送时必须带相同 token
- `LINUXDO_BROWSER_FALLBACK_HEADLESS`
  默认 `true`，如果无头被 Cloudflare 拦截，会自动重试有头浏览器

### 邮件通知

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_SENDER`
- `SMTP_RECIPIENT`
- `SMTP_USE_TLS`

### 可选 LLM 增强

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

如果不配置这些变量，系统会只使用规则识别，不影响抓取和入库。
