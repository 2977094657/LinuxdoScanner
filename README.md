# LinuxdoScanner

<p align="center">
  <img src="logo.jpg" alt="LinuxdoScanner Logo" width="180">
</p>

LinuxdoScanner 用来持续跟踪 `linux.do` 的新主题。它会复用你当前浏览器里的登录态抓取内容，再交给本地后端做筛选、摘要和通知判断；命中你关心的话题时可以直接推送到飞书，不重要的帖子则安静落库。

## 功能

- 复用当前浏览器登录态抓取新主题
- 抓完主题立即拉详情并分批处理
- 用 AI 做摘要、标签和通知判断
- 支持把命中的内容推送到飞书
- 在扩展设置页里查看同步状态、爬取数据和配置项
- Windows 支持托盘常驻和开机自启动

## 快速开始

1. `uv sync`
2. `uv run main.py`
3. 在 Chrome 的 `chrome://extensions/` 加载 `chrome-extension`
4. 打开扩展并配置本地服务地址

默认配置文件在 `config/settings.toml`。Windows 下默认会显示托盘图标，如需以前台方式运行，可使用 `uv run main.py bridge-server --no-tray`。
