# LinuxdoScanner

<p align="center">
  <img src="logo.jpg" alt="LinuxdoScanner Logo" width="180">
</p>

为了解决在 `linux.do` 上因为害怕错过新内容而不得不不停刷帖、把大量时间耗在不感兴趣话题上的问题，LinuxdoScanner 会用 AI 持续关注你真正关心的内容，把值得看的主题筛出来，把不重要的信息留在后台，并在命中重点时即时推送到飞书、邮件等通知渠道，让你不用一直盯着论坛也能跟上重点。

## 当前实现

现在它已经可以作为一个长期运行的个人内容跟踪工具来使用，帮你把注意力留给真正想看的内容。

## 安装

```bash
python -m pip install -e .
```

## 使用方式

本项目现在只保留 Chrome 扩展桥接方案，不再提供调试浏览器、镜像目录或直接抓取的备选路径。

### Chrome 扩展桥接当前浏览器

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
- 分页抓取间隔默认 10 秒，连续翻 10 页后会自动休息 3 分钟

5. 点击扩展里的“立即同步”

扩展会直接在你当前登录的浏览器里完成同步，本地服务负责入库、筛选和通知：

- 检查 `linux.do` 登录 Cookie
- 拉取最新主题与详情 JSON
- 将数据 POST 到本地 `bridge-server`
- 复用现有 SQLite、AI 标注、邮件通知逻辑

如果你希望接收即时提醒，可以在扩展设置页里配置 AI 关注点，并按需保存飞书通知目标；邮件通知仍然可以通过环境变量配置。

如果你设置了：

```bash
set LINUXDO_REQUIRE_LOGIN=1
```

那么当前浏览器未登录 `linux.do` 时，扩展同步会直接报错，不会只抓公开主题。

## 数据文件

- SQLite: `data/linuxdo.sqlite3`

当前主要表：

- `topics`: 主题数据、正文摘要、AI 标注、通知状态
- `crawler_state`: 增量游标，例如 `last_seen_topic_id`

## 重要环境变量

- `LINUXDO_BOOTSTRAP_LIMIT`
  默认 `30`
- `LINUXDO_MAX_PAGES_PER_RUN`
  默认 `10`
- `LINUXDO_REQUIRE_LOGIN`
  设为 `1` 后，如果当前浏览器未登录 `linux.do`，程序会直接报错，而不是只抓公开主题
- `LINUXDO_BRIDGE_HOST`
  本地扩展桥接服务监听地址，默认 `127.0.0.1`
- `LINUXDO_BRIDGE_PORT`
  本地扩展桥接服务端口，默认 `8765`
- `LINUXDO_BRIDGE_TOKEN`
  可选。设置后，Chrome 扩展向本地服务推送时必须带相同 token

### 飞书通知

- 可在扩展设置页直接保存飞书配置并发送测试消息
- `LARK_CLI_PATH`
- `FEISHU_CHAT_ID`
- `FEISHU_USER_ID`

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
