from __future__ import annotations

from collections.abc import Callable
import ctypes
import logging
import os
import tempfile
import threading
import webbrowser
from pathlib import Path

from PIL import Image, ImageOps

from .runtime_paths import app_root, bundle_root
from .settings import Settings


LOGGER = logging.getLogger(__name__)


class TrayUnavailableError(RuntimeError):
    pass


if os.name == "nt":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    IMAGE_ICON = 1
    LR_DEFAULTSIZE = 0x00000040
    LR_LOADFROMFILE = 0x00000010
    IDI_APPLICATION = ctypes.c_wchar_p(32512)

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004

    MF_SEPARATOR = 0x00000800
    MF_STRING = 0x00000000
    MF_DISABLED = 0x00000002
    MF_GRAYED = 0x00000001

    TPM_NONOTIFY = 0x0080
    TPM_RETURNCMD = 0x0100
    TPM_RIGHTBUTTON = 0x0002

    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    WM_LBUTTONDBLCLK = 0x0203
    WM_APP = 0x8000
    WM_NULL = 0x0000
    WM_TRAYICON = WM_APP + 1

    class POINT(ctypes.Structure):
        _fields_ = [
            ("x", wintypes.LONG),
            ("y", wintypes.LONG),
        ]

    UINT_PTR = ctypes.c_size_t
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = ctypes.c_ushort
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HANDLE,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.LoadIconW.restype = wintypes.HICON
    user32.CreatePopupMenu.restype = wintypes.HANDLE
    user32.AppendMenuW.argtypes = [wintypes.HANDLE, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR]
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.LPVOID,
    ]
    user32.TrackPopupMenu.restype = wintypes.UINT
    user32.DestroyMenu.argtypes = [wintypes.HANDLE]
    user32.DestroyMenu.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE


class BackendTrayApp:
    _MENU_OPEN = 1001
    _MENU_EXIT = 1002

    def __init__(self, settings: Settings, *, stop_callback: Callable[[], None]) -> None:
        if os.name != "nt":
            raise TrayUnavailableError("系统托盘仅支持 Windows。")
        self.settings = settings
        self.stop_callback = stop_callback
        self._tooltip = "LinuxDoScanner 后端运行中"
        self._window_class_name = "LinuxDoScannerBackendTray"
        self._instance_handle = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._window_proc)
        self._hwnd: int | None = None
        self._notify_data = NOTIFYICONDATAW()
        self._icon_handle = None
        self._owns_icon = False
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._stopping = False
        self._stop_lock = threading.Lock()

    def run(self) -> None:
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = self._instance_handle
        window_class.lpszClassName = self._window_class_name
        atom = user32.RegisterClassW(ctypes.byref(window_class))
        if not atom:
            raise TrayUnavailableError("无法注册系统托盘窗口类。")
        try:
            self._create_window()
            self._add_tray_icon()
            self._message_loop()
        finally:
            self._cleanup()
            user32.UnregisterClassW(self._window_class_name, self._instance_handle)

    def stop(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def _create_window(self) -> None:
        hwnd = user32.CreateWindowExW(
            0,
            self._window_class_name,
            self._tooltip,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            self._instance_handle,
            None,
        )
        if not hwnd:
            raise TrayUnavailableError("无法创建系统托盘宿主窗口。")
        self._hwnd = hwnd

    def _message_loop(self) -> None:
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == WM_TRAYICON:
            if lparam == WM_LBUTTONDBLCLK:
                self._open_linuxdo()
                return 0
            if lparam in {WM_LBUTTONUP, WM_RBUTTONUP}:
                self._show_menu()
                return 0
        if message == WM_CLOSE:
            self._handle_exit()
            return 0
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self) -> None:
        if self._hwnd is None:
            return
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(
                menu,
                MF_STRING | MF_DISABLED | MF_GRAYED,
                0,
                "LinuxDoScanner 后端运行中",
            )
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, self._MENU_OPEN, "打开 Linux.do")
            user32.AppendMenuW(menu, MF_STRING, self._MENU_EXIT, "退出后端")
            cursor = POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            user32.SetForegroundWindow(self._hwnd)
            command = user32.TrackPopupMenu(
                menu,
                TPM_NONOTIFY | TPM_RETURNCMD | TPM_RIGHTBUTTON,
                cursor.x,
                cursor.y,
                0,
                self._hwnd,
                None,
            )
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
            if command == self._MENU_OPEN:
                self._open_linuxdo()
            elif command == self._MENU_EXIT:
                self._handle_exit()
        finally:
            user32.DestroyMenu(menu)

    def _handle_exit(self) -> None:
        with self._stop_lock:
            if self._stopping:
                return
            self._stopping = True
        try:
            self.stop_callback()
        except Exception as exc:
            LOGGER.warning("Stopping backend from tray failed: %s", exc)
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None

    def _open_linuxdo(self) -> None:
        try:
            webbrowser.open(self.settings.base_url)
        except Exception as exc:
            LOGGER.warning("Unable to open browser from tray: %s", exc)

    def _add_tray_icon(self) -> None:
        if self._hwnd is None:
            raise TrayUnavailableError("系统托盘窗口尚未创建。")
        icon_path = self._prepare_icon_file()
        self._icon_handle = user32.LoadImageW(
            None,
            str(icon_path),
            IMAGE_ICON,
            0,
            0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        self._owns_icon = bool(self._icon_handle)
        if not self._icon_handle:
            self._icon_handle = user32.LoadIconW(None, IDI_APPLICATION)
            self._owns_icon = False
        self._notify_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self._notify_data.hWnd = self._hwnd
        self._notify_data.uID = 1
        self._notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._notify_data.uCallbackMessage = WM_TRAYICON
        self._notify_data.hIcon = self._icon_handle
        self._notify_data.szTip = self._tooltip
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._notify_data)):
            raise TrayUnavailableError("无法将图标加入系统托盘。")

    def _cleanup(self) -> None:
        try:
            if self._notify_data.hWnd:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._notify_data))
                self._notify_data.hWnd = None
        except Exception:
            pass
        if self._owns_icon and self._icon_handle:
            try:
                user32.DestroyIcon(self._icon_handle)
            except Exception:
                pass
        self._icon_handle = None
        self._owns_icon = False
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    def _prepare_icon_file(self) -> Path:
        logo_path = self._find_logo_path()
        if logo_path is None or not logo_path.exists():
            raise TrayUnavailableError("未找到可用于系统托盘的 logo.jpg。")
        if self._temp_dir is None:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="linuxdoscanner-tray-")
        icon_path = Path(self._temp_dir.name) / "tray-icon.ico"
        with Image.open(logo_path) as image:
            normalized = ImageOps.contain(image.convert("RGBA"), (64, 64))
            canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            offset_x = max(0, (64 - normalized.width) // 2)
            offset_y = max(0, (64 - normalized.height) // 2)
            canvas.paste(normalized, (offset_x, offset_y), normalized)
            canvas.save(icon_path, format="ICO", sizes=[(64, 64), (32, 32), (16, 16)])
        return icon_path

    def _find_logo_path(self) -> Path | None:
        candidates = [
            app_root() / "logo.jpg",
            bundle_root() / "logo.jpg",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None
