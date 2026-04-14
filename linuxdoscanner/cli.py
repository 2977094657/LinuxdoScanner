from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

from loguru import logger

from .bridge import ExtensionBridgeServer
from .logging_utils import configure_logging
from .runtime_paths import app_root, bootstrap_bundled_directory
from .settings import Settings
from .windows_startup import (
    WindowsStartupManager,
    is_bridge_server_healthy,
    launch_browser_after_delay,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Linux.do 新主题监控器")
    parser.add_argument("--debug", action="store_true", help="输出更详细的日志")
    parser.set_defaults(command="bridge-server")

    subparsers = parser.add_subparsers(dest="command")
    bridge_server_parser = subparsers.add_parser("bridge-server", help="启动给 Chrome 扩展使用的本地桥接服务")
    bridge_server_parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Windows 下禁用系统托盘，直接以前台服务方式运行",
    )
    subparsers.add_parser("bridge-info", help="打印 Chrome 扩展桥接的服务地址与扩展目录")
    startup_install_parser = subparsers.add_parser("startup-install", help="启用 Windows 开机自启动")
    startup_install_parser.add_argument("--launch-browser", action="store_true", help="登录系统时顺带唤醒浏览器")
    startup_install_parser.add_argument("--browser-url", default="", help="唤醒浏览器时打开的地址")
    subparsers.add_parser("startup-remove", help="关闭 Windows 开机自启动")
    subparsers.add_parser("startup-status", help="查看 Windows 开机自启动状态")
    startup_run_parser = subparsers.add_parser("startup-run", help=argparse.SUPPRESS)
    startup_run_parser.add_argument("--launch-browser", action="store_true", help=argparse.SUPPRESS)
    startup_run_parser.add_argument("--browser-url", default="", help=argparse.SUPPRESS)
    startup_run_parser.add_argument("--browser-executable", default="", help=argparse.SUPPRESS)
    startup_run_parser.add_argument("--no-tray", action="store_true", help=argparse.SUPPRESS)
    return parser


def _run_server_forever(
    server: ExtensionBridgeServer,
    *,
    launch_browser_target: tuple[str, str] | None = None,
) -> int:
    try:
        if launch_browser_target is not None:
            threading.Thread(
                target=launch_browser_after_delay,
                args=launch_browser_target,
                daemon=True,
            ).start()
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("正在停止后端服务 ...")
    finally:
        server.close()
    return 0


def _run_server_with_tray(
    server: ExtensionBridgeServer,
    settings: Settings,
    *,
    launch_browser_target: tuple[str, str] | None = None,
) -> int:
    from .windows_tray import BackendTrayApp, TrayUnavailableError

    try:
        tray = BackendTrayApp(settings, stop_callback=server.stop)
    except TrayUnavailableError as exc:
        logger.warning("系统托盘初始化失败，将退回无托盘模式：{}", exc)
        return _run_server_forever(
            server,
            launch_browser_target=launch_browser_target,
        )

    server_error: list[BaseException] = []

    def serve() -> None:
        try:
            server.serve_forever()
        except BaseException as exc:  # pragma: no cover - defensive background capture
            server_error.append(exc)

    server_thread = threading.Thread(
        target=serve,
        name="linuxdoscanner-bridge-server",
        daemon=True,
    )
    server_thread.start()

    if launch_browser_target is not None:
        threading.Thread(
            target=launch_browser_after_delay,
            args=launch_browser_target,
            daemon=True,
        ).start()

    def watch_server_exit() -> None:
        server_thread.join()
        tray.stop()

    watcher_thread = threading.Thread(
        target=watch_server_exit,
        name="linuxdoscanner-tray-watchdog",
        daemon=True,
    )
    watcher_thread.start()

    try:
        tray.run()
    except KeyboardInterrupt:
        logger.info("正在停止系统托盘 ...")
    finally:
        server.stop()
        server_thread.join(timeout=5)

    if server_error:
        raise server_error[0]
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "bridge-server"

    bootstrap_bundled_directory("config")
    bootstrap_bundled_directory("chrome-extension")
    settings = Settings.from_env()
    settings.ensure_directories()
    if args.command in {"bridge-server", "startup-run"} or args.debug:
        info_log_path, error_log_path = configure_logging(debug=args.debug, settings=settings)
        logger.info("Info logs are being written to {}", info_log_path)
        logger.info("Error logs are being written to {}", error_log_path)

    startup_manager = WindowsStartupManager(settings)

    if args.command == "bridge-info":
        extension_dir = app_root() / "chrome-extension"
        print(f"bridge_url: http://{settings.bridge_host}:{settings.bridge_port}")
        print(f"extension_dir: {extension_dir}")
        print(f"token_required: {'yes' if settings.bridge_token else 'no'}")
        if settings.bridge_token:
            print(f"bridge_token: {settings.bridge_token}")
        return 0

    if args.command == "startup-status":
        print(json.dumps(startup_manager.status().to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "startup-install":
        try:
            status = startup_manager.install(
                launch_browser=bool(args.launch_browser),
                browser_url=args.browser_url or None,
            )
        except (RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print(f"已启用 Windows 开机自启动：{status.script_path}")
        if status.launch_browser:
            print(f"登录时会尝试唤醒浏览器：{status.browser_url}")
        else:
            print("登录时不会额外唤醒浏览器。")
        return 0

    if args.command == "startup-remove":
        status = startup_manager.remove()
        if status.enabled:
            print("未能移除 Windows 开机自启动脚本。", file=sys.stderr)
            return 1
        print("已移除 Windows 开机自启动脚本。")
        return 0

    if args.command == "startup-run":
        browser_url = args.browser_url or startup_manager.default_browser_url()
        browser_executable = args.browser_executable.strip() or settings.browser_executable
        launch_browser_target = None
        if args.launch_browser and browser_executable:
            launch_browser_target = (browser_executable, browser_url)

        if is_bridge_server_healthy(settings):
            logger.info("Bridge server is already running, skipping duplicate startup instance.")
            if launch_browser_target is not None:
                launch_browser_after_delay(browser_executable, browser_url, delay_seconds=0)
            elif args.launch_browser:
                logger.warning("Skipping browser wake-up because no browser executable is configured.")
            return 0

        server = ExtensionBridgeServer(settings)
        if args.launch_browser and launch_browser_target is None:
            logger.warning("Skipping browser wake-up because no browser executable is configured.")
        if os.name == "nt" and not args.no_tray:
            return _run_server_with_tray(
                server,
                settings,
                launch_browser_target=launch_browser_target,
            )
        return _run_server_forever(
            server,
            launch_browser_target=launch_browser_target,
        )

    if args.command == "bridge-server":
        server = ExtensionBridgeServer(settings)
        if os.name == "nt" and not getattr(args, "no_tray", False):
            return _run_server_with_tray(server, settings)
        return _run_server_forever(server)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
