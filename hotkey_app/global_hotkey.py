"""
Global hotkey integration for the Uzbek Text Simplifier.

ВАЖНО: здесь НЕТ новой ML-логики. Единственная задача этого модуля —
системная интеграция: перехват глобальной комбинации клавиш (работает
даже когда окно приложения не в фокусе), эмуляция Ctrl+C для захвата
выделенного пользователем текста и вызов уже существующей функции
инференса (ModelRunner.simplify(), см. app_tkinter.py) с этим текстом
вместо текста из поля ввода UI.

Backends:
    - "keyboard"  — предпочтительный, умеет и регистрацию хоткеев, и
      эмуляцию нажатий (keyboard.send). На Linux/macOS требует root
      (см. README), на Windows иногда требует запуска от администратора.
    - "pynput"    — fallback, если keyboard недоступен/не удалось
      импортировать. Регистрация через pynput.keyboard.GlobalHotKeys,
      эмуляция Ctrl+C через pynput.keyboard.Controller.

Публичный API:
    register_hotkey(combo, callback) -> HotkeyHandle
    HotkeyHandle.unregister()
    HotkeyRegistrationError
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

_BACKEND: Optional[str] = None

try:
    import keyboard as _keyboard_lib  # type: ignore

    _BACKEND = "keyboard"
except Exception:  # noqa: BLE001 - импорт может падать по многим причинам (нет прав, нет X11 и т.д.)
    _keyboard_lib = None

if _BACKEND is None:
    try:
        from pynput import keyboard as _pynput_keyboard  # type: ignore

        _BACKEND = "pynput"
    except Exception:  # noqa: BLE001
        _pynput_keyboard = None


class HotkeyRegistrationError(RuntimeError):
    """Хоткей не удалось зарегистрировать (занят / нет прав / нет бэкенда)."""


class HotkeyHandle:
    """Хэндл на зарегистрированный хоткей, позволяет его снять."""

    def __init__(self, unregister_fn: Callable[[], None]):
        self._unregister_fn = unregister_fn
        self._active = True

    def unregister(self) -> None:
        if not self._active:
            return
        try:
            self._unregister_fn()
        finally:
            self._active = False


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalize_combo_for_pynput(combo: str) -> str:
    """"ctrl+alt+s" -> "<ctrl>+<alt>+s" (формат pynput.GlobalHotKeys)."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    modifiers = {"ctrl", "alt", "shift", "cmd", "super", "win"}
    out = []
    for p in parts:
        if p in modifiers:
            # pynput использует <cmd> для win/super
            if p in ("win", "super"):
                p = "cmd"
            out.append(f"<{p}>")
        else:
            out.append(p)
    return "+".join(out)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def backend_name() -> Optional[str]:
    """Какой бэкенд активен (для логов/README/диагностики), либо None."""
    return _BACKEND


def register_hotkey(combo: str, callback: Callable[[], None]) -> HotkeyHandle:
    """Регистрирует глобальный хоткей `combo` (например "ctrl+alt+s").

    При вызове хоткея `callback` вызывается без аргументов из фонового
    потока библиотеки-хоткея (НЕ из потока Tkinter) — вызывающий код
    должен сам передать управление в Tkinter через `after()`, если нужно
    трогать UI.

    Бросает HotkeyRegistrationError, если комбинация занята, нет прав
    или ни один бэкенд не доступен. Никогда не падает молча.
    """
    if not combo or not combo.strip():
        raise HotkeyRegistrationError("Пустая комбинация клавиш (hotkey_combo)")

    if _BACKEND == "keyboard":
        try:
            _keyboard_lib.add_hotkey(combo, callback, suppress=False)
        except Exception as e:  # noqa: BLE001
            raise HotkeyRegistrationError(
                f"Не удалось зарегистрировать хоткей '{combo}' (backend=keyboard): {e}. "
                "Возможные причины: комбинация уже занята другим приложением, "
                "или процессу не хватает прав (см. README — раздел про права администратора/root)."
            ) from e

        def _unregister():
            try:
                _keyboard_lib.remove_hotkey(combo)
            except Exception:  # noqa: BLE001
                logger.warning("Не удалось снять хоткей '%s' (backend=keyboard)", combo)

        logger.info("Global hotkey '%s' registered (backend=keyboard)", combo)
        return HotkeyHandle(_unregister)

    if _BACKEND == "pynput":
        try:
            pynput_combo = _normalize_combo_for_pynput(combo)
            listener = _pynput_keyboard.GlobalHotKeys({pynput_combo: callback})
            listener.start()
        except Exception as e:  # noqa: BLE001
            raise HotkeyRegistrationError(
                f"Не удалось зарегистрировать хоткей '{combo}' (backend=pynput): {e}. "
                "Возможные причины: комбинация уже занята, либо нет доступа к "
                "системным событиям клавиатуры (см. README, например macOS требует "
                "разрешения 'Accessibility' / 'Input Monitoring')."
            ) from e

        def _unregister():
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                logger.warning("Не удалось снять хоткей '%s' (backend=pynput)", combo)

        logger.info("Global hotkey '%s' registered (backend=pynput)", combo)
        return HotkeyHandle(_unregister)

    raise HotkeyRegistrationError(
        "Ни один backend для глобальных хоткеев не доступен "
        "(не установлен ни 'keyboard', ни 'pynput' — см. requirements.txt), "
        "либо импорт упал из-за нехватки прав/окружения (нет X11 и т.п.)."
    )


# ---------------------------------------------------------------------------
# Clipboard capture (эмуляция Ctrl+C + poll буфера обмена)
# ---------------------------------------------------------------------------

def _send_copy() -> None:
    """Эмулирует Ctrl+C на системном уровне (не через Tkinter bind)."""
    if _BACKEND == "keyboard":
        _keyboard_lib.send("ctrl+c")
        return
    if _BACKEND == "pynput":
        controller = _pynput_keyboard.Controller()
        with controller.pressed(_pynput_keyboard.Key.ctrl):
            controller.press("c")
            controller.release("c")
        return
    raise HotkeyRegistrationError("Нет доступного backend для эмуляции Ctrl+C")


def capture_selected_text(timeout: float = 1.0, poll_interval: float = 0.03) -> Optional[str]:
    """Копирует текущее выделение в буфер и возвращает его.

    Эмулирует Ctrl+C, затем поллит pyperclip.paste() до тех пор, пока
    значение буфера не изменится относительно того, что было до копирования,
    либо пока не истечёт `timeout` секунд (НЕ жёсткий time.sleep — poll с
    ретраями).

    Возвращает:
        - новый текст буфера, если он появился/изменился до истечения таймаута
        - None, если буфер не изменился за отведённое время (значит либо
          ничего не было выделено, либо копирование не сработало)
    """
    import pyperclip

    try:
        before = pyperclip.paste()
    except Exception:  # noqa: BLE001
        before = None

    _send_copy()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            current = pyperclip.paste()
        except Exception:  # noqa: BLE001
            current = None

        if current is not None and current != before and current.strip():
            return current

        time.sleep(poll_interval)

    # Таймаут истёк: буфер не обновился (значит либо ничего не было
    # выделено, либо копирование по какой-то причине не сработало).
    return None
