from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .browser_state import BrowserProfileError, build_managed_debug_profile, detect_browser_profile
from .bridge import ExtensionBridgeServer
from .discourse import resolve_cdp_websocket_url
from .service import LinuxDoMonitor
from .settings import Settings


def configure_logging(debug: bool, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                log_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            ),
        ],
        force=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Linux.do 新主题监控器")
    parser.add_argument("--debug", action="store_true", help="输出更详细的日志")

    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="首次启动时打开浏览器完成登录并保存会话")
    auth_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="等待手动登录完成的最长秒数，默认读取环境变量或 900 秒",
    )
    auth_parser.add_argument(
        "--isolated",
        action="store_true",
        help="强制使用独立 profile 登录，而不是复用当前用户浏览器 profile",
    )
    subparsers.add_parser("cdp-command", help="打印可用于 CDP 的调试浏览器启动命令")
    subparsers.add_parser("start-debug-browser", help="复制当前 profile 到受控目录并启动可连接的调试浏览器")

    run_once_parser = subparsers.add_parser("run-once", help="执行一次抓取")
    run_once_parser.add_argument(
        "--bootstrap-limit",
        type=int,
        default=None,
        help="首次启动时默认抓取的主题数量，默认读取环境变量或 30",
    )

    poll_parser = subparsers.add_parser("poll", help="每隔固定秒数持续轮询")
    poll_parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="轮询间隔秒数，默认读取环境变量或 300",
    )

    subparsers.add_parser("probe", help="检查原始 API 与浏览器 fallback 的可用性")
    subparsers.add_parser("bridge-server", help="启动给 Chrome 扩展使用的本地桥接服务")
    subparsers.add_parser("bridge-info", help="打印 Chrome 扩展桥接的服务地址与扩展目录")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    settings.ensure_directories()
    configure_logging(args.debug, settings.data_dir / "linuxdoscanner.log")
    logging.getLogger(__name__).info("Logs are being written to %s", (settings.data_dir / "linuxdoscanner.log"))

    if args.command == "auth":
        if settings.browser_executable is None:
            parser.error("未找到 Chrome/Edge，可通过 LINUXDO_BROWSER_EXECUTABLE 指定浏览器路径。")
        from .discourse import BrowserSessionError, BrowserSessionManager

        if settings.browser_cdp_url:
            print("检测到已配置 CDP 调试浏览器。")
            print("程序会在当前调试浏览器中新开标签页，请直接在那个浏览器里完成 linux.do 登录。")
            print("登录成功后会自动保存会话，然后返回。")

        try:
            metadata = BrowserSessionManager(settings).capture_login(
                wait_timeout_seconds=args.timeout,
                isolated=args.isolated,
            )
        except BrowserSessionError as exc:
            parser.exit(status=2, message=f"auth 失败: {exc}\n")
        logging.getLogger(__name__).info("Session captured successfully at %s", metadata.captured_at)
        return 0

    if args.command == "cdp-command":
        profile = detect_browser_profile(settings)
        if profile is None:
            parser.exit(status=2, message="cdp-command 失败: 未检测到可复用的 Chrome/Edge profile。\n")
        executable = settings.browser_executable or profile.executable_path
        if executable is None:
            parser.exit(status=2, message="未检测到浏览器可执行文件路径。\n")
        print(f"browser: {profile.browser}")
        print(f"profile: {profile.profile_name}")
        print(f"mirror_user_data_dir: {settings.browser_debug_profile_dir / profile.browser}")
        print()
        print("推荐流程：")
        print("1. 先完全关闭当前浏览器")
        print("2. 运行: python main.py start-debug-browser")
        print("3. 如果后续日志提示 linux.do 未登录，再执行: python main.py auth")
        print()
        print("然后在同一个终端执行：")
        print('$env:LINUXDO_BROWSER_CDP_URL="http://127.0.0.1:9222"')
        print("python main.py poll")
        print()
        print("注意：镜像目录会完整复制并显示进度，但新版 Chrome/Windows 可能不会保留 linux.do 登录态。")
        print("这时不需要重新开新浏览器，只要对当前调试浏览器执行 `python main.py auth` 即可。")
        print()
        print("如果你想手工启动，也请使用镜像目录而不是默认真实用户目录：")
        print(
            f'& "{executable}" --remote-debugging-port=9222 '
            f'--user-data-dir="{settings.browser_debug_profile_dir / profile.browser}" '
            f'--profile-directory="{profile.profile_name}"'
        )
        return 0

    if args.command == "start-debug-browser":
        try:
            print("正在准备调试浏览器镜像目录...")
            profile = build_managed_debug_profile(settings, status_callback=print)
        except BrowserProfileError as exc:
            parser.exit(status=2, message=f"start-debug-browser 失败: {exc}\n")
        executable = settings.browser_executable or profile.executable_path
        if executable is None:
            parser.exit(status=2, message="未检测到浏览器可执行文件路径。\n")
        process = subprocess.Popen(
            [
                executable,
                "--remote-debugging-port=9222",
                f"--user-data-dir={profile.user_data_dir}",
                f"--profile-directory={profile.profile_name}",
            ]
        )
        cdp_url = "http://127.0.0.1:9222"
        deadline = time.time() + 15
        while time.time() < deadline:
            if process.poll() is not None:
                parser.exit(status=2, message="start-debug-browser 失败: 调试浏览器启动后立即退出了。\n")
            try:
                resolve_cdp_websocket_url(cdp_url)
                break
            except Exception:
                time.sleep(0.5)
        else:
            parser.exit(status=2, message="start-debug-browser 失败: 9222 调试端口没有成功启动。\n")

        print("调试浏览器已启动，并已确认 9222 端口可访问。")
        print("注意：镜像会尽量保留浏览器状态，但新版 Chrome/Windows 下 linux.do 登录态可能不会直接继承。")
        print("如果后续日志提示未登录，请在这个调试浏览器上执行一次 `python main.py auth`。")
        print('$env:LINUXDO_BROWSER_CDP_URL="http://127.0.0.1:9222"')
        print("python main.py auth")
        print("python main.py poll")
        return 0

    if args.command == "bridge-info":
        extension_dir = Path(__file__).resolve().parent.parent / "chrome-extension"
        print(f"bridge_url: http://{settings.bridge_host}:{settings.bridge_port}")
        print(f"extension_dir: {extension_dir}")
        print(f"token_required: {'yes' if settings.bridge_token else 'no'}")
        if settings.bridge_token:
            print(f"bridge_token: {settings.bridge_token}")
        return 0

    if args.command == "bridge-server":
        server = ExtensionBridgeServer(settings)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("正在停止 bridge-server ...")
        finally:
            server.close()
        return 0

    monitor = LinuxDoMonitor(settings)
    try:
        if args.command == "run-once":
            payloads = monitor.run_once(bootstrap_limit=args.bootstrap_limit)
            logging.getLogger(__name__).info("Captured %s topics in this run.", len(payloads))
            return 0
        if args.command == "poll":
            try:
                monitor.run_forever(interval_seconds=args.interval)
            except Exception as exc:
                parser.exit(status=2, message=f"poll 失败: {exc}\n")
            return 0
        if args.command == "probe":
            try:
                results = monitor.probe()
            except Exception as exc:
                parser.exit(status=2, message=f"probe 失败: {exc}\n")
            for key, value in results.items():
                print(f"{key}: {value}")
            return 0
        parser.error(f"Unknown command: {args.command}")
        return 2
    finally:
        monitor.close()


if __name__ == "__main__":
    sys.exit(main())
