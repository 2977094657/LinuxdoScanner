# LinuxdoScanner

<p align="center">
  <img src="logo.jpg" alt="LinuxdoScanner Logo" width="180">
</p>

为了解决在 `linux.do` 上因为害怕错过新内容而不得不不停刷帖、把大量时间耗在不感兴趣话题上的问题，LinuxdoScanner 会用 AI 持续关注你真正关心的内容，把值得看的主题筛出来，把不重要的信息留在后台，并在命中重点时即时推送到飞书、邮件等通知渠道，让你不用一直盯着论坛也能跟上重点。

## 当前实现

现在它已经可以作为一个长期运行的个人内容跟踪工具来使用，帮你把注意力留给真正想看的内容。

## 安装

```bash
uv sync
```

依赖新增和项目维护也建议统一使用 `uv`，例如 `uv add <package>`、`uv sync`、`uv run main.py ...`。

## 使用方式

本项目现在只保留 Chrome 扩展桥接方案，不再提供调试浏览器、镜像目录或直接抓取的备选路径。

## 配置约定

- 默认启动方式：`uv run main.py`
- 项目配置：根目录 `config/settings.toml`
- 日志目录：`output/logs/YYYY/MM/DD/`
- 普通日志：`DD_info.log`
- 错误日志：`DD_error.log`
- SQLite：`output/databases/linuxdo.sqlite3`
- 评测和其他导出文件：统一放在 `output/`

### Chrome 扩展桥接当前浏览器

1. 在终端启动本地接收服务：

```bash
uv run main.py
```

如果你更喜欢显式写法，`uv run main.py bridge-server` 也继续可用。
在 Windows 下，默认会显示系统托盘图标；如果你只想以前台方式运行，可以改用：

```bash
uv run main.py bridge-server --no-tray
```

2. 查看扩展目录和本地服务地址：

```bash
uv run main.py bridge-info
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
- 每轮默认最多抓 10 页
- 分页请求会在 `1-10` 秒之间随机等待
- 每轮结束后会在 `1-180` 秒之间随机等待，再继续下一轮，直到碰到已同步边界

5. 点击扩展里的“立即同步”

扩展会直接在你当前登录的浏览器里完成同步，本地服务负责入库、筛选和通知：

- 检查 `linux.do` 登录 Cookie
- 拉取最新主题与详情 JSON
- 将数据 POST 到本地 `bridge-server`
- 复用现有 SQLite、AI 标注、邮件通知逻辑

扩展设置页现在已经支持：

- 运行概览与同步进度查看
- 桥接参数、随机延迟区间和 Windows 开机启动配置
- 已入库爬取数据的分页查看、筛选与搜索
- AI 关注点、飞书通知目标与测试消息管理

如果你希望接收即时提醒，可以在扩展设置页里配置 AI 关注点，并按需保存飞书通知目标；邮件通知仍然可以通过环境变量配置。

如果你设置了：

```bash
set LINUXDO_REQUIRE_LOGIN=1
```

那么当前浏览器未登录 `linux.do` 时，扩展同步会直接报错，不会只抓公开主题。

### Windows 开机启动

如果你希望电脑登录后自动拉起本地后端，可以在扩展设置页的“桥接设置 -> Windows 开机启动”里直接开启。

- 开启后，会在当前 Windows 用户的 Startup 目录里写入一个启动脚本
- 下次登录系统时，会自动拉起本地 `bridge-server`
- 后端启动后会常驻系统托盘，托盘图标使用当前项目 logo，可从托盘菜单直接退出后端
- 如果同时开启“唤醒浏览器”，脚本还会尝试顺带启动 Chrome / Edge，让扩展也一起恢复工作
- 如果没有自动检测到浏览器路径，可以在 `config/settings.toml` 的 `[browser].executable` 里手动指定

也可以手动用命令管理：

```bash
uv run main.py startup-install --launch-browser
uv run main.py startup-status
uv run main.py startup-remove
```

## 构建 Windows EXE

如果你希望把后端单独分发给用户，可以把本地 bridge-server 打成 Windows exe。

先安装构建依赖：

```bash
uv sync --extra build
```

然后执行：

```bash
uv run python scripts/build_windows_exe.py
```

构建结果会输出到：

- `dist/LinuxDoScannerBackend/LinuxDoScannerBackend.exe`

这是一个 `onedir` 分发目录，旁边会同时带上：

- `config/`
- `chrome-extension/`

这样 exe 运行时会默认从自身所在目录读取 `config/settings.toml`，开机自启动脚本在打包版下也会直接启动这个 exe，不再依赖 `python main.py`。
Windows 版本启动后也会显示系统托盘图标，便于用户确认后端存活并主动退出。

## GitHub Actions 自动发布

仓库根目录已经包含发布工作流：

- 文件位置：`.github/workflows/release.yml`
- 触发方式：只在推送 tag 时触发
- tag 格式：建议使用 `v1.0.0` 这种语义化版本号

发布时会自动生成两个 Release 产物：

- `LinuxDoScannerBackend-vX.Y.Z-windows-x64.zip`
- `LinuxDoScannerExtension-vX.Y.Z.zip`

其中扩展包会在打包时自动把 `chrome-extension/manifest.json` 里的 `version` 同步成 tag 对应的版本号，便于用户直接解压后加载。

注意：

- 仅推送分支或普通 commit 不会触发这个发布工作流
- 只有推送符合 `v*` 规则的 tag，才会开始构建后端压缩包、扩展压缩包并发布 GitHub Release

常用命令：

```bash
git tag v1.0.0
git push origin v1.0.0
```

## 数据文件

- SQLite: `output/databases/linuxdo.sqlite3`

当前主要表：

- `topics`: 主题数据、正文摘要、AI 标注、通知状态
- `crawler_state`: 增量游标，例如 `last_seen_topic_id`

## 配置文件

默认读取根目录的 `config/settings.toml`，环境变量仍然可以覆盖对应字段。

## 重要环境变量

- `LINUXDO_BOOTSTRAP_LIMIT`
  默认 `30`
- `LINUXDO_MAX_PAGES_PER_RUN`
  默认 `10`，表示每轮最多抓取多少页
- `LINUXDO_PAGE_REQUEST_DELAY_MIN_SECONDS`
  默认 `1`
- `LINUXDO_PAGE_REQUEST_DELAY_MAX_SECONDS`
  默认 `10`
- `LINUXDO_ROUND_DELAY_MIN_SECONDS`
  默认 `1`
- `LINUXDO_ROUND_DELAY_MAX_SECONDS`
  默认 `180`
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
