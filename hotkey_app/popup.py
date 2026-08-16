"""
Popup-окно, показывающее результат упрощения текста рядом с курсором.

Это чистая UI-обвязка: сам инференс делает уже существующий
ModelRunner.simplify() (см. app_tkinter.py) — здесь он просто
запускается в отдельном потоке, чтобы не блокировать Tkinter mainloop,
а UI обновляется через after() из главного потока.
"""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    import pyautogui
    _HAS_PYAUTOGUI = True
except Exception:  # noqa: BLE001
    _HAS_PYAUTOGUI = False

# Локальная копия палитры, чтобы popup.py не тянул тяжёлые импорты
# из app_tkinter.py (tkinterdnd2, torch и т.п.) только ради констант.
_COLORS = {
    "bg": "#0b0f1a",
    "border": "#243257",
    "text_fg": "#dbe4ff",
    "subdued_fg": "#7d8bb3",
    "accent": "#3b6bff",
    "accent_hover": "#5b8bff",
}

_POPUP_WIDTH = 420
_POPUP_MAX_HEIGHT = 320


def _get_cursor_position(root: tk.Tk) -> tuple[int, int]:
    if _HAS_PYAUTOGUI:
        try:
            pos = pyautogui.position()
            return int(pos.x), int(pos.y)
        except Exception:  # noqa: BLE001
            pass
    # Fallback: tkinter умеет получать позицию курсора относительно экрана
    return root.winfo_pointerx(), root.winfo_pointery()


class SimplifierPopup(tk.Toplevel):
    """Popup без рамки окна, позиционируется рядом с курсором мыши.

    Автозакрытие: клик вне окна, Esc, либо по кнопке "Копировать" после
    (опционально) — копия не закрывает окно, чтобы пользователь мог
    убедиться, что скопировалось нужное.
    """

    def __init__(
        self,
        master: tk.Misc,
        source_text: str,
        infer_fn: Callable[[str], str],
        on_close: Optional[Callable[[], None]] = None,
    ):
        super().__init__(master)
        self._on_close = on_close
        self._result_text: Optional[str] = None
        self._closed = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=_COLORS["border"])

        self._build_ui()
        self._position_near_cursor()

        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<FocusOut>", self._on_focus_out)
        # Клик вне окна закрывает popup: ловим глобальный клик через bind_all
        # на короткое время после показа (сразу после создания сам клик,
        # которым был вызван хоткей, ещё может "пролетать" — избегаем гонки
        # небольшой задержкой перед активацией обработчика).
        self.after(150, self._arm_click_outside_handler)

        self.focus_force()

        # Инференс — в отдельном потоке, чтобы не блокировать mainloop.
        # Модель НЕ создаётся заново — infer_fn должен быть замыканием,
        # переиспользующим уже загруженный ModelRunner.
        self._start_time = time.monotonic()
        threading.Thread(
            target=self._run_inference, args=(source_text, infer_fn), daemon=True
        ).start()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg=_COLORS["border"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)

        inner = tk.Frame(outer, bg=_COLORS["bg"], padx=14, pady=12)
        inner.pack(fill="both", expand=True)

        header = tk.Frame(inner, bg=_COLORS["bg"])
        header.pack(fill="x")

        tk.Label(
            header,
            text="✨ Soddalashtirilmoqda...",
            bg=_COLORS["bg"],
            fg=_COLORS["subdued_fg"],
            font=("Segoe UI", 9, "italic"),
        ).pack(side="left")

        self._status_label = header.winfo_children()[0]

        close_btn = tk.Label(
            header, text="✕", bg=_COLORS["bg"], fg=_COLORS["subdued_fg"],
            font=("Segoe UI", 10, "bold"), cursor="hand2",
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda _e: self.close())

        body = tk.Frame(inner, bg=_COLORS["bg"])
        body.pack(fill="both", expand=True, pady=(8, 8))

        self._text_widget = tk.Text(
            body,
            width=46,
            height=6,
            wrap="word",
            bg=_COLORS["bg"],
            fg=_COLORS["text_fg"],
            font=("Segoe UI", 10),
            relief="flat",
            state="disabled",
            highlightthickness=0,
            padx=2, pady=2,
        )
        self._text_widget.pack(fill="both", expand=True)
        self._set_body_text("⏳ Kutilmoqda...")

        footer = tk.Frame(inner, bg=_COLORS["bg"])
        footer.pack(fill="x")

        self._copy_btn = tk.Button(
            footer,
            text="📋 Копировать",
            command=self._copy_result,
            bg=_COLORS["accent"],
            fg="#ffffff",
            activebackground=_COLORS["accent_hover"],
            activeforeground="#ffffff",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padx=10, pady=4,
            state="disabled",
            cursor="hand2",
        )
        self._copy_btn.pack(side="left")

        self._timing_label = tk.Label(
            footer, text="", bg=_COLORS["bg"], fg=_COLORS["subdued_fg"],
            font=("Segoe UI", 8),
        )
        self._timing_label.pack(side="right")

    def _set_body_text(self, text: str) -> None:
        self._text_widget.config(state="normal")
        self._text_widget.delete("1.0", "end")
        self._text_widget.insert("1.0", text)
        self._text_widget.config(state="disabled")

    def _position_near_cursor(self) -> None:
        x, y = _get_cursor_position(self.master)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        offset = 16
        pos_x = x + offset
        pos_y = y + offset

        # Не даём окну вылезти за правый/нижний край экрана.
        if pos_x + _POPUP_WIDTH > screen_w:
            pos_x = max(0, x - _POPUP_WIDTH - offset)
        if pos_y + _POPUP_MAX_HEIGHT > screen_h:
            pos_y = max(0, y - _POPUP_MAX_HEIGHT - offset)

        self.geometry(f"{_POPUP_WIDTH}x{_POPUP_MAX_HEIGHT}+{pos_x}+{pos_y}")

    # ------------------------------------------------------------------
    # Inference (background thread) -> UI update (main thread via after())
    # ------------------------------------------------------------------
    def _run_inference(self, source_text: str, infer_fn: Callable[[str], str]) -> None:
        try:
            result = infer_fn(source_text)
            err: Optional[str] = None
        except Exception as e:  # noqa: BLE001
            result = None
            err = str(e)
            logger.exception("Ошибка инференса из global-hotkey popup")

        elapsed = time.monotonic() - self._start_time

        def apply():
            if self._closed:
                return
            self._on_inference_done(result, err, elapsed)

        # Обновление UI обязано идти через after() из главного потока Tkinter.
        try:
            self.after(0, apply)
        except tk.TclError:
            pass  # окно уже уничтожено

    def _on_inference_done(self, result: Optional[str], err: Optional[str], elapsed: float) -> None:
        if err:
            self._status_label.config(text="⚠️ Xatolik")
            self._set_body_text(f"Xatolik yuz berdi:\n{err}")
            self._timing_label.config(text=f"{elapsed:.2f}s")
            return

        self._result_text = result or ""
        self._status_label.config(text="✨ Tayyor")
        self._set_body_text(self._result_text or "(bo'sh natija)")
        self._copy_btn.config(state="normal" if self._result_text else "disabled")
        self._timing_label.config(text=f"{elapsed:.2f}s")

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def _copy_result(self) -> None:
        if not self._result_text:
            return
        import pyperclip

        try:
            pyperclip.copy(self._result_text)
            self._copy_btn.config(text="✅ Nusxalandi")
            self.after(1200, lambda: self._copy_btn.config(text="📋 Копировать"))
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось скопировать результат в буфер")

    def _arm_click_outside_handler(self) -> None:
        if self._closed:
            return
        self.bind_all("<Button-1>", self._on_click_outside, add="+")

    def _on_click_outside(self, event: tk.Event) -> None:
        widget = event.widget
        # Если клик пришёлся не на потомка этого Toplevel — закрываем.
        w = widget
        while w is not None:
            if w == self:
                return
            w = getattr(w, "master", None)
        self.close()

    def _on_focus_out(self, _event: tk.Event) -> None:
        # Небольшая защита: FocusOut может сработать при переключении между
        # дочерними виджетами popup'а — закрываем только если фокус ушёл
        # действительно за пределы окна (проверяем через after_idle).
        self.after_idle(self._check_focus)

    def _check_focus(self) -> None:
        if self._closed:
            return
        try:
            focused = self.focus_get()
        except KeyError:
            focused = None
        if focused is None:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.unbind_all("<Button-1>")
        except Exception:  # noqa: BLE001
            pass
        if self._on_close:
            try:
                self._on_close()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.destroy()
        except tk.TclError:
            pass


def show_popup(
    root: tk.Tk,
    text: str,
    infer_fn: Callable[[str], str],
    on_close: Optional[Callable[[], None]] = None,
) -> SimplifierPopup:
    """Показывает popup с результатом упрощения `text` рядом с курсором.

    `infer_fn` — функция text -> simplified_text; вызывающий код должен
    передать замыкание над уже загруженным ModelRunner (например,
    `lambda t: app.runner.simplify(t, num_beams)`), а не создавать новую
    модель.

    Должна вызываться из главного потока Tkinter (например, через
    root.after(0, ...) из обработчика хоткея, который сам исполняется в
    фоновом потоке библиотеки хоткеев).
    """
    return SimplifierPopup(root, text, infer_fn, on_close=on_close)
