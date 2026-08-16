"""
Конфигурация параметров "агрессивности" упрощения.

ВАЖНО: это НЕ conditioning через дообучение модели. mT5-small,
используемая в этом приложении, была fine-tuned только на task-prefix
"simplify: " (см. finetune_mt5_small.ipynb в корне репозитория). Параметры
из этого модуля влияют на генерацию исключительно через модификацию
текстового префикса, который дописывается перед input-текстом
(prompt-инжиниринг поверх уже обученной модели). Модель НЕ видела такую
расширенную разметку префикса на этапе обучения, поэтому эффект от
aggressiveness/drop_* нужно проверять эмпирически (см. scripts/ab_compare.py),
а не считать гарантированным.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal, get_args

logger = logging.getLogger(__name__)

Aggressiveness = Literal["conservative", "balanced", "aggressive"]

_VALID_AGGRESSIVENESS = get_args(Aggressiveness)
_MAX_LENGTH_RATIO_RANGE = (0.3, 1.0)

CONFIG_FILENAME = "config.json"
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME)


@dataclass
class SimplifierConfig:
    aggressiveness: Aggressiveness = "balanced"
    max_length_ratio: float = 0.6
    drop_dates: bool = False
    drop_law_refs: bool = False
    drop_stats: bool = False
    # Задел под будущий global hotkey (следующая фича) — не используется
    # нигде в текущей логике генерации/UI, чтобы не трогать этот файл
    # повторно, когда hotkey будет реализовываться.
    hotkey_combo: str = "ctrl+alt+s"

    # ------------------------------------------------------------------
    # Валидация отдельных полей
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_field(name: str, value: Any, default: Any) -> Any:
        """Возвращает value, если оно валидно для поля name, иначе default
        (с предупреждением в лог). Валидация всегда идёт по одному полю —
        битое значение одного поля не должно откатывать весь конфиг."""
        try:
            if name == "aggressiveness":
                if value in _VALID_AGGRESSIVENESS:
                    return value
                raise ValueError(f"'{value}' not in {_VALID_AGGRESSIVENESS}")

            if name == "max_length_ratio":
                v = float(value)
                lo, hi = _MAX_LENGTH_RATIO_RANGE
                if lo <= v <= hi:
                    return v
                raise ValueError(f"{v} outside range {_MAX_LENGTH_RATIO_RANGE}")

            if name in ("drop_dates", "drop_law_refs", "drop_stats"):
                if isinstance(value, bool):
                    return value
                raise ValueError(f"{value!r} is not bool")

            if name == "hotkey_combo":
                if isinstance(value, str) and value.strip():
                    return value
                raise ValueError(f"{value!r} is not a non-empty string")

            # Неизвестное поле (например, из будущей версии конфига) —
            # просто пропускаем его как есть.
            return value
        except (ValueError, TypeError) as e:
            logger.warning(
                "config: invalid value for field '%s' (%r): %s — falling back to default %r",
                name, value, e, default,
            )
            return default

    # ------------------------------------------------------------------
    # load / save
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | None = None) -> "SimplifierConfig":
        """Загружает конфиг из JSON рядом с приложением.

        - Файла нет -> создаётся с default-значениями, дефолт возвращается.
        - Файл битый JSON -> предупреждение в лог, возвращается полностью
          default-конфиг (без попытки частичного парсинга битого файла).
        - Отдельное поле вне диапазона / не из Literal / неверного типа ->
          предупреждение в лог, для ЭТОГО поля используется default,
          остальные валидные поля сохраняются.
        """
        path = path or _CONFIG_PATH
        default = cls()

        if not os.path.exists(path):
            default.save(path)
            return default

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("config: %s is corrupted (%s) — using defaults", path, e)
            return default

        if not isinstance(raw, dict):
            logger.warning("config: %s does not contain a JSON object — using defaults", path)
            return default

        values: dict[str, Any] = {}
        for f_ in fields(cls):
            default_value = getattr(default, f_.name)
            if f_.name in raw:
                values[f_.name] = cls._validate_field(f_.name, raw[f_.name], default_value)
            else:
                values[f_.name] = default_value

        return cls(**values)

    def save(self, path: str | None = None) -> None:
        path = path or _CONFIG_PATH
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2, sort_keys=True)


def build_prefix(config: SimplifierConfig) -> str:
    """Строит task-prefix для model.generate() на основе конфига.

    ЭФФЕКТ НЕ ГАРАНТИРОВАН: mT5-small в этом проекте fine-tuned только на
    строке "simplify: " (см. docstring модуля). Расширенный формат префикса
    ("simplify [key=value, ...]: ") модель не видела на этапе обучения — это
    prompt-инжиниринг поверх уже обученной модели, а не conditioning через
    дообучение. Проверяйте влияние параметров вручную/через ab_compare.py,
    не полагайтесь на то, что они всегда меняют поведение предсказуемо.

    Обратная совместимость: конфиг по умолчанию (aggressiveness="balanced",
    все drop_*=False) даёт байт-в-байт "simplify: ", как и раньше — потому
    что в prefix попадают только параметры, отличающиеся от default.
    """
    default = SimplifierConfig()
    flags: list[str] = []

    if config.aggressiveness != default.aggressiveness:
        flags.append(f"aggressiveness={config.aggressiveness}")

    if config.max_length_ratio != default.max_length_ratio:
        flags.append(f"max_length={config.max_length_ratio}")

    if config.drop_dates:
        flags.append("drop_dates=true")
    if config.drop_law_refs:
        flags.append("drop_law_refs=true")
    if config.drop_stats:
        flags.append("drop_stats=true")

    if not flags:
        return "simplify: "

    return f"simplify [{', '.join(flags)}]: "
