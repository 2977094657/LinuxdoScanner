from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

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
        self._progress_lock = threading.Lock()
        self._progress_state = self._empty_progress_state()
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
                parsed = bridge._parse_path(self.path)
                if parsed.path == "/api/bridge/health":
                    bridge._write_json(self, HTTPStatus.OK, {"ok": True, "now": utc_now()})
                    return
                if parsed.path == "/api/bridge/state":
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
                if parsed.path == "/api/bridge/progress":
                    requested_sync_run_id = parsed.query.get("sync_run_id", [""])[0].strip()
                    bridge._write_json(
                        self,
                        HTTPStatus.OK,
                        bridge._get_progress_state(sync_run_id=requested_sync_run_id),
                    )
                    return
                if parsed.path == "/api/bridge/ai-config":
                    bridge._write_json(
                        self,
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "config": bridge.ai_config_manager.load_config().to_dict(),
                        },
                    )
                    return
                if parsed.path == "/api/bridge/notification-config":
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
                parsed = bridge._parse_path(self.path)
                if parsed.path == "/api/bridge/ai-config":
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
                if parsed.path == "/api/bridge/notification-config":
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
                parsed = bridge._parse_path(self.path)
                try:
                    payload = bridge._read_json(self)
                except ValueError as exc:
                    bridge._write_json(
                        self,
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "invalid_json", "detail": str(exc)},
                    )
                    return

                if parsed.path == "/api/bridge/ai-config/sync-models":
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

                if parsed.path == "/api/bridge/notification-config/test":
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

                if parsed.path == "/api/bridge/crawl-data/clear":
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

                if parsed.path != "/api/bridge/push":
                    bridge._write_json(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
                    return

                logged_in = bool(payload.get("logged_in"))
                sync_run_id = str(payload.get("sync_run_id") or "").strip()
                bridge.monitor.database.set_state("bridge_last_sync_at", utc_now())
                bridge.monitor.database.set_state("bridge_logged_in", "1" if logged_in else "0")

                if bridge.settings.require_login and not logged_in:
                    bridge._set_progress_state(
                        sync_run_id=sync_run_id,
                        in_progress=False,
                        percent=0,
                        stage="login-required",
                        label="同步失败",
                        detail="当前浏览器未登录 linux.do，请先登录后再同步。",
                    )
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

                bridge._set_progress_state(
                    sync_run_id=sync_run_id,
                    in_progress=True,
                    percent=89,
                    stage="bridge-ingest",
                    label="整理服务端任务",
                    detail="正在准备入库与 AI 识别流程",
                )
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

                bridge._set_progress_state(
                    sync_run_id=sync_run_id,
                    in_progress=True,
                    percent=89,
                    stage="bridge-ingest",
                    label="接收扩展数据",
                    detail=f"收到 {len(topic_documents)} 个主题，正在交给本地服务处理",
                )

                def on_monitor_progress(progress: dict[str, Any]) -> None:
                    bridge._set_progress_state(
                        sync_run_id=sync_run_id,
                        in_progress=bool(progress.get("percent", 0) < 100),
                        percent=int(progress.get("percent") or 0),
                        stage=str(progress.get("stage") or ""),
                        label=str(progress.get("label") or ""),
                        detail=str(progress.get("detail") or ""),
                    )

                try:
                    with bridge._lock:
                        stored = bridge.monitor.ingest_topic_documents(
                            topic_documents,
                            category_map=category_map,
                            progress_callback=on_monitor_progress,
                        )
                except Exception as exc:
                    bridge._set_progress_state(
                        sync_run_id=sync_run_id,
                        in_progress=False,
                        percent=0,
                        stage="error",
                        label="同步失败",
                        detail=str(exc),
                    )
                    bridge._write_json(
                        self,
                        HTTPStatus.BAD_GATEWAY,
                        {"ok": False, "error": "ingest_failed", "detail": str(exc)},
                    )
                    return

                bridge._set_progress_state(
                    sync_run_id=sync_run_id,
                    in_progress=False,
                    percent=100,
                    stage="completed",
                    label="同步完成",
                    detail=f"本轮新增入库 {len(stored)} 个主题",
                )

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

    def _parse_path(self, raw_path: str) -> Any:
        parsed = urlsplit(raw_path)
        return SimpleNamespace(path=parsed.path, query=parse_qs(parsed.query))

    def _empty_progress_state(self, *, sync_run_id: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "in_progress": False,
            "sync_run_id": sync_run_id,
            "percent": 0,
            "stage": "",
            "label": "",
            "detail": "",
            "updated_at": utc_now(),
        }

    def _set_progress_state(
        self,
        *,
        sync_run_id: str,
        in_progress: bool,
        percent: int,
        stage: str,
        label: str,
        detail: str,
    ) -> None:
        snapshot = {
            "ok": True,
            "in_progress": in_progress,
            "sync_run_id": sync_run_id,
            "percent": max(0, min(100, int(percent))),
            "stage": stage,
            "label": label,
            "detail": detail,
            "updated_at": utc_now(),
        }
        with self._progress_lock:
            self._progress_state = snapshot

    def _get_progress_state(self, *, sync_run_id: str = "") -> dict[str, Any]:
        with self._progress_lock:
            snapshot = dict(self._progress_state)
        if sync_run_id and snapshot.get("sync_run_id") and snapshot["sync_run_id"] != sync_run_id:
            return self._empty_progress_state(sync_run_id=sync_run_id)
        return snapshot

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
