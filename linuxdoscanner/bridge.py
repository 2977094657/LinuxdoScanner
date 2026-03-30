from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .ai_config import AIConfigManager
from .notification_config import NotificationConfigManager
from .service import LinuxDoMonitor
from .settings import Settings
from .storage import utc_now


LOGGER = logging.getLogger(__name__)


class ExtensionBridgeServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.monitor = LinuxDoMonitor(settings, enable_client=False)
        self.ai_config_manager = AIConfigManager(settings, self.monitor.database)
        self.notification_config_manager = NotificationConfigManager(settings, self.monitor.database)
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(
            (self.settings.bridge_host, self.settings.bridge_port),
            self._build_handler(),
        )

    def _build_handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "LinuxDoBridge/0.1"

            def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                bridge._write_json(self, HTTPStatus.NO_CONTENT, None)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if not bridge._authorize(self):
                    return
                if self.path == "/api/bridge/health":
                    bridge._write_json(self, HTTPStatus.OK, {"ok": True, "now": utc_now()})
                    return
                if self.path == "/api/bridge/state":
                    bridge._write_json(
                        self,
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "base_url": bridge.settings.base_url,
                            "bootstrap_limit": bridge.settings.bootstrap_limit,
                            "max_pages_per_run": bridge.settings.max_pages_per_run,
                            "last_seen_topic_id": bridge.monitor.database.get_last_seen_topic_id(),
                            "require_login": bridge.settings.require_login,
                        },
                    )
                    return
                if self.path == "/api/bridge/ai-config":
                    bridge._write_json(
                        self,
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "config": bridge.ai_config_manager.load_config().to_dict(),
                        },
                    )
                    return
                if self.path == "/api/bridge/notification-config":
                    bridge._write_json(
                        self,
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "config": bridge.notification_config_manager.load_config().to_dict(),
                        },
                    )
                    return
                bridge._write_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

            def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if not bridge._authorize(self):
                    return
                if self.path == "/api/bridge/ai-config":
                    try:
                        payload = bridge._read_json(self)
                        config = bridge.ai_config_manager.save_config(payload)
                    except ValueError as exc:
                        bridge._write_json(
                            self,
                            HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "invalid_ai_config", "detail": str(exc)},
                        )
                        return
                    bridge._write_json(self, HTTPStatus.OK, {"ok": True, "config": config.to_dict()})
                    return
                if self.path == "/api/bridge/notification-config":
                    try:
                        payload = bridge._read_json(self)
                        config = bridge.notification_config_manager.save_config(payload)
                    except ValueError as exc:
                        bridge._write_json(
                            self,
                            HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "invalid_notification_config", "detail": str(exc)},
                        )
                        return
                    bridge.monitor.refresh_notifier()
                    bridge._write_json(self, HTTPStatus.OK, {"ok": True, "config": config.to_dict()})
                    return
                bridge._write_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if not bridge._authorize(self):
                    return
                try:
                    payload = bridge._read_json(self)
                except ValueError as exc:
                    bridge._write_json(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "invalid_json", "detail": str(exc)},
                    )
                    return

                if self.path == "/api/bridge/ai-config/sync-models":
                    try:
                        with bridge._lock:
                            config = bridge.ai_config_manager.sync_models(payload if payload else None)
                    except ValueError as exc:
                        bridge._write_json(
                            self,
                            HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "invalid_ai_config", "detail": str(exc)},
                        )
                        return
                    except RuntimeError as exc:
                        bridge._write_json(
                            self,
                            HTTPStatus.BAD_GATEWAY,
                            {"ok": False, "error": "sync_models_failed", "detail": str(exc)},
                        )
                        return
                    bridge._write_json(self, HTTPStatus.OK, {"ok": True, "config": config.to_dict()})
                    return

                if self.path == "/api/bridge/notification-config/test":
                    try:
                        with bridge._lock:
                            if payload:
                                bridge.notification_config_manager.save_config(payload)
                                bridge.monitor.refresh_notifier()
                            bridge.monitor.notifier.send_test_message()
                    except Exception as exc:
                        bridge._write_json(
                            self,
                            HTTPStatus.BAD_GATEWAY,
                            {"ok": False, "error": "notification_test_failed", "detail": str(exc)},
                        )
                        return
                    bridge._write_json(
                        self,
                        HTTPStatus.OK,
                        {"ok": True, "message": "飞书测试消息已发送。"},
                    )
                    return

                if self.path == "/api/bridge/crawl-data/clear":
                    with bridge._lock:
                        cleared = bridge.monitor.database.clear_crawl_data()
                    bridge._write_json(
                        self,
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "cleared": cleared,
                            "message": "已清空抓取数据，AI 配置保持不变。",
                        },
                    )
                    return

                if self.path != "/api/bridge/push":
                    bridge._write_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
                    return

                logged_in = bool(payload.get("logged_in"))
                bridge.monitor.database.set_state("bridge_last_sync_at", utc_now())
                bridge.monitor.database.set_state("bridge_logged_in", "1" if logged_in else "0")

                if bridge.settings.require_login and not logged_in:
                    bridge._write_json(
                        self,
                        HTTPStatus.CONFLICT,
                        {
                            "ok": False,
                            "error": "login_required",
                            "detail": "当前浏览器未登录 linux.do，请先在浏览器里登录后再同步。",
                        },
                    )
                    return

                categories = payload.get("categories") or []
                category_map: dict[int, str] = {}
                for item in categories:
                    if not isinstance(item, dict):
                        continue
                    if "id" not in item or "name" not in item:
                        continue
                    try:
                        category_map[int(item["id"])] = str(item["name"])
                    except Exception:
                        continue

                topic_documents = payload.get("topics") or []
                if not isinstance(topic_documents, list):
                    bridge._write_json(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "invalid_topics", "detail": "`topics` 必须是数组。"},
                    )
                    return

                with bridge._lock:
                    stored = bridge.monitor.ingest_topic_documents(topic_documents, category_map=category_map)

                bridge._write_json(
                    self,
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "stored_count": len(stored),
                        "last_seen_topic_id": bridge.monitor.database.get_last_seen_topic_id(),
                        "logged_in": logged_in,
                    },
                )

            def log_message(self, format: str, *args) -> None:
                LOGGER.info("Bridge %s - %s", self.address_string(), format % args)

        return Handler

    def serve_forever(self) -> None:
        LOGGER.info(
            "Extension bridge server listening on http://%s:%s",
            self.settings.bridge_host,
            self.settings.bridge_port,
        )
        try:
            self._server.serve_forever()
        finally:
            self.close()

    def close(self) -> None:
        try:
            self._server.server_close()
        except Exception:
            pass
        self.monitor.close()

    def _authorize(self, handler: BaseHTTPRequestHandler) -> bool:
        expected = self.settings.bridge_token
        if expected and handler.headers.get("X-LinuxDo-Bridge-Token") != expected:
            self._write_json(handler, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return False
        return True

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        content_length = int(handler.headers.get("Content-Length", "0"))
        raw = handler.rfile.read(content_length)
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body 必须是对象。")
        return data

    def _write_json(
        self,
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: dict[str, Any] | None,
    ) -> None:
        origin = handler.headers.get("Origin")
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        if origin and origin.startswith("chrome-extension://"):
            handler.send_header("Access-Control-Allow-Origin", origin)
            handler.send_header("Vary", "Origin")
            handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-LinuxDo-Bridge-Token")
            handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        handler.end_headers()
        if body:
            handler.wfile.write(body)
