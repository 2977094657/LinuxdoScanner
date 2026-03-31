from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .bridge import ExtensionBridgeServer
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

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
