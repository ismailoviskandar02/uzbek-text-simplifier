"""
Системный трей для Uzbek Text Simplifier.

Приложение при закрытии главного окна не завершает процесс, а
сворачивается в трей (иначе глобальный хоткей перестал бы работать,
т.к. процесс был бы убит). Из иконки в трее доступны пункты меню:
Открыть / Настройки / Выход.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except Exception:  # noqa: BLE001
    _TRAY_AVAILABLE = False


def _build_default_icon_image():
    """Простая иконка-заглушка (буква 'S' на синем фоне), чтобы не
    зависеть от наличия .ico/.png файла в репозитории."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 2, size - 2], fill=(59, 107, 255, 255))
    draw.text((size // 2 - 8, size // 2 - 12), "S", fill="#ffffff")
    return img


class TrayIcon:
    """Обёртка над pystray.Icon, управляемая из главного потока Tkinter.

    pystray.Icon.run() блокирует поток, в котором вызван — поэтому иконка
    всегда запускается в отдельном демоническом потоке, а обработчики меню
    возвращаются в Tkinter через root.after(0, ...), т.к. трогать виджеты
    Tkinter не из главного потока небезопасно.
    """

    def __init__(
        self,
        root: tk.Tk,
        on_open: Callable[[], None],
        on_settings: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
        icon_image=None,
    ):
        if not _TRAY_AVAILABLE:
            raise RuntimeError(
                "pystray/Pillow не установлены — трей недоступен. "
                "Добавьте их в окружение (см. requirements.txt)."
            )

        self._root = root
        self._on_open = on_open
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None

        image = icon_image or _build_default_icon_image()

        menu_items = [pystray.MenuItem("Открыть", self._handle_open, default=True)]
        if on_settings:
            menu_items.append(pystray.MenuItem("Настройки", self._handle_settings))
        menu_items.append(pystray.MenuItem("Выход", self._handle_quit))

        self._icon = pystray.Icon(
            "uzbek_text_simplifier",
            icon=image,
            title="Uzbek Text Simplifier",
            menu=pystray.Menu(*menu_items),
        )

    # ------------------------------------------------------------------
    # Menu callbacks — выполняются в потоке pystray, поэтому возвращаем
    # управление в Tkinter через after(0, ...).
    # ------------------------------------------------------------------
    def _handle_open(self, _icon=None, _item=None) -> None:
        self._root.after(0, self._on_open)

    def _handle_settings(self, _icon=None, _item=None) -> None:
        if self._on_settings:
            self._root.after(0, self._on_settings)

    def _handle_quit(self, _icon=None, _item=None) -> None:
        def _quit():
            self.stop()
            if self._on_quit:
                self._on_quit()
            else:
                self._root.destroy()

        self._root.after(0, _quit)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        logger.info("Tray icon started")

    def stop(self) -> None:
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось корректно остановить tray icon")

    def notify(self, message: str, title: str = "Uzbek Text Simplifier") -> None:
        """Короткий toast/уведомление через трей (поддерживается не на
        всех платформах — pystray делает best-effort)."""
        try:
            if self._icon is not None:
                self._icon.notify(message, title)
        except Exception:  # noqa: BLE001
            logger.debug("Tray notify недоступен на этой платформе")


def is_tray_available() -> bool:
    return _TRAY_AVAILABLE


def minimize_to_tray(root: tk.Tk, tray: TrayIcon) -> None:
    """Скрывает главное окно и (если ещё не запущена) запускает иконку трея."""
    tray.start()
    root.withdraw()


def restore_from_tray(root: tk.Tk) -> None:
    """Возвращает главное окно из трея."""
    root.deiconify()
    root.lift()
    root.focus_force()
