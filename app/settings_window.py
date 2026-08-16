"""
Окно настроек параметров упрощения (aggressiveness, max_length_ratio,
drop_*). Работает поверх app.config.SimplifierConfig; никакой отдельной
модели/обучения не подразумевает — см. docstring в config.py.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from config import SimplifierConfig

# Минимальная локализация именно этого окна (три языка, как в основном
# приложении). Полноценный LANGS-словарь app.py трогать не обязательно —
# окно самодостаточно.
_TEXT = {
    "ru": {
        "window_title": "Настройки упрощения",
        "aggressiveness_label": "Агрессивность упрощения",
        "conservative": "Консервативно",
        "balanced": "Сбалансированно",
        "aggressive": "Агрессивно",
        "max_length_label": "Максимальная длина вывода (доля от входа)",
        "drop_dates": "Убирать даты",
        "drop_law_refs": "Убирать ссылки на законы/статьи",
        "drop_stats": "Убирать статистику/числа",
        "save": "Сохранить",
        "reset": "Сброс к умолчаниям",
        "saved_ok": "Настройки сохранены",
    },
    "uz": {
        "window_title": "Soddalashtirish sozlamalari",
        "aggressiveness_label": "Soddalashtirish agressivligi",
        "conservative": "Konservativ",
        "balanced": "Muvozanatli",
        "aggressive": "Agressiv",
        "max_length_label": "Chiqish maksimal uzunligi (kirishga nisbatan)",
        "drop_dates": "Sanalarni olib tashlash",
        "drop_law_refs": "Qonun/modda havolalarini olib tashlash",
        "drop_stats": "Statistika/raqamlarni olib tashlash",
        "save": "Saqlash",
        "reset": "Standartga qaytarish",
        "saved_ok": "Sozlamalar saqlandi",
    },
    "en": {
        "window_title": "Simplification settings",
        "aggressiveness_label": "Simplification aggressiveness",
        "conservative": "Conservative",
        "balanced": "Balanced",
        "aggressive": "Aggressive",
        "max_length_label": "Max output length (ratio of input)",
        "drop_dates": "Drop dates",
        "drop_law_refs": "Drop law/article references",
        "drop_stats": "Drop statistics/numbers",
        "save": "Save",
        "reset": "Reset to defaults",
        "saved_ok": "Settings saved",
    },
}


class SettingsWindow(tk.Toplevel):
    """Toplevel с настройками SimplifierConfig.

    on_saved(config): вызывается после успешного save() с новым
    SimplifierConfig, чтобы вызывающий код (SimplifierApp) мог обновить
    свой current_config без перезапуска приложения.
    """

    def __init__(
        self,
        master: tk.Misc,
        lang_code: str = "ru",
        on_saved: Callable[[SimplifierConfig], None] | None = None,
        set_status: Callable[[str], None] | None = None,
    ):
        super().__init__(master)
        self._on_saved = on_saved
        self._set_status = set_status
        self._t = _TEXT.get(lang_code, _TEXT["ru"])

        self.title(self._t["window_title"])
        self.resizable(False, False)
        self.transient(master)

        # Подгружаем текущий конфиг с диска, а не создаём с нуля —
        # так окно всегда открывается с актуальными сохранёнными значениями.
        self.config_obj = SimplifierConfig.load()

        self._build_ui()
        self.grab_set()

    # ------------------------------------------------------------------
    def _build_ui(self):
        t = self._t
        pad = {"padx": 12, "pady": 6}

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        # --- aggressiveness ---
        ttk.Label(frame, text=t["aggressiveness_label"], font=("Segoe UI", 10, "bold")).pack(
            anchor="w", **pad
        )
        self.aggressiveness_var = tk.StringVar(value=self.config_obj.aggressiveness)
        radio_frame = ttk.Frame(frame)
        radio_frame.pack(fill="x", padx=12)
        for value in ("conservative", "balanced", "aggressive"):
            ttk.Radiobutton(
                radio_frame, text=t[value], value=value, variable=self.aggressiveness_var
            ).pack(anchor="w")

        # --- max_length_ratio ---
        ttk.Label(frame, text=t["max_length_label"], font=("Segoe UI", 10, "bold")).pack(
            anchor="w", **pad
        )
        self.max_length_var = tk.DoubleVar(value=self.config_obj.max_length_ratio)
        self.max_length_value_label = ttk.Label(frame, text=f"{self.max_length_var.get():.2f}")
        self.max_length_value_label.pack(anchor="e", padx=12)
        max_length_scale = ttk.Scale(
            frame, from_=0.3, to=1.0, orient="horizontal",
            variable=self.max_length_var, command=self._on_max_length_change,
        )
        max_length_scale.pack(fill="x", padx=12)

        # --- drop_* checkboxes ---
        ttk.Label(frame, text="", font=("Segoe UI", 4)).pack()  # small spacer
        self.drop_dates_var = tk.BooleanVar(value=self.config_obj.drop_dates)
        self.drop_law_refs_var = tk.BooleanVar(value=self.config_obj.drop_law_refs)
        self.drop_stats_var = tk.BooleanVar(value=self.config_obj.drop_stats)

        ttk.Checkbutton(frame, text=t["drop_dates"], variable=self.drop_dates_var).pack(
            anchor="w", padx=12, pady=2
        )
        ttk.Checkbutton(frame, text=t["drop_law_refs"], variable=self.drop_law_refs_var).pack(
            anchor="w", padx=12, pady=2
        )
        ttk.Checkbutton(frame, text=t["drop_stats"], variable=self.drop_stats_var).pack(
            anchor="w", padx=12, pady=2
        )

        # --- buttons ---
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(16, 0))

        ttk.Button(btn_row, text=t["reset"], command=self._on_reset).pack(side="left")
        ttk.Button(btn_row, text=t["save"], command=self._on_save).pack(side="right")

    # ------------------------------------------------------------------
    def _on_max_length_change(self, _value=None):
        self.max_length_value_label.config(text=f"{self.max_length_var.get():.2f}")

    def _on_reset(self):
        default = SimplifierConfig()
        self.aggressiveness_var.set(default.aggressiveness)
        self.max_length_var.set(default.max_length_ratio)
        self._on_max_length_change()
        self.drop_dates_var.set(default.drop_dates)
        self.drop_law_refs_var.set(default.drop_law_refs)
        self.drop_stats_var.set(default.drop_stats)

    def _on_save(self):
        new_config = SimplifierConfig(
            aggressiveness=self.aggressiveness_var.get(),
            max_length_ratio=round(self.max_length_var.get(), 2),
            drop_dates=self.drop_dates_var.get(),
            drop_law_refs=self.drop_law_refs_var.get(),
            drop_stats=self.drop_stats_var.get(),
            hotkey_combo=self.config_obj.hotkey_combo,
        )
        new_config.save()
        self.config_obj = new_config

        if self._on_saved:
            self._on_saved(new_config)

        # Подтверждение сохранения: используем существующий status bar
        # приложения, если он передан, вместо создания нового UI-элемента.
        if self._set_status:
            self._set_status(self._t["saved_ok"])
        else:
            messagebox.showinfo(self._t["window_title"], self._t["saved_ok"])
