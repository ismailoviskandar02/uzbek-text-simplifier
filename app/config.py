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
ModelVariant = Literal["small", "basic"]

_VALID_AGGRESSIVENESS = get_args(Aggressiveness)
_VALID_MODEL_VARIANTS = get_args(ModelVariant)
_MAX_LENGTH_RATIO_RANGE = (0.3, 1.0)
_NUM_BEAMS_RANGE = (1, 8)

CONFIG_FILENAME = "config.json"
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME)


@dataclass
class SimplifierConfig:
    # Окно настроек убрано из UI — все параметры генерации зафиксированы
    # на значениях, дающих максимальную точность/качество упрощения
    # (не пользовательские, подобраны эмпирически под задачу).
    aggressiveness: Aggressiveness = "balanced"
    # Вариант модели на Hugging Face Hub (subfolder в репозитории MODEL_ID):
    # "small" — облегчённая/более быстрая версия, "basic" — базовая версия.
    model_variant: ModelVariant = "basic"
    max_length_ratio: float = 0.75
    # num_beams=8 — верхняя граница диапазона (_NUM_BEAMS_RANGE), даёт
    # наиболее точный/качественный beam search за счёт скорости.
    num_beams: int = 8
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

            if name == "model_variant":
                if value in _VALID_MODEL_VARIANTS:
                    return value
                raise ValueError(f"'{value}' not in {_VALID_MODEL_VARIANTS}")

            if name == "max_length_ratio":
                v = float(value)
                lo, hi = _MAX_LENGTH_RATIO_RANGE
                if lo <= v <= hi:
                    return v
                raise ValueError(f"{v} outside range {_MAX_LENGTH_RATIO_RANGE}")

            if name == "num_beams":
                v = int(value)
                lo, hi = _NUM_BEAMS_RANGE
                if lo <= v <= hi:
                    return v
                raise ValueError(f"{v} outside range {_NUM_BEAMS_RANGE}")

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
    """Строит task-prefix для model.generate().

    ФИКС БАГА "в вывод отправляется системный конфиг": mT5-small в этом
    проекте fine-tuned ТОЛЬКО на строке "simplify: ". Раньше сюда
    подмешивались флаги конфига вида "simplify [aggressiveness=..., ...]: ",
    как только конфиг отличался от дефолтного (а он отличается уже в
    config.json из коробки: aggressiveness="conservative", max_length_ratio=1.0
    против дефолтных "balanced"/0.75). Модель на этапе обучения такой
    расширенный формат не видела — для неё это out-of-distribution текст,
    и вместо упрощения она начинала зацикленно копировать этот промпт
    обратно в вывод (видно на скриншоте: "qwerty (conservative,
    max_length=1.0): qwerty (conservative, max_length=1.0): ...").

    Поэтому build_prefix теперь ВСЕГДА возвращает ровно "simplify: ",
    независимо от конфига. Параметры aggressiveness/max_length_ratio/
    drop_* не текст-инжинирятся в промпт — их эффект реализуется через
    настоящие параметры генерации, см. build_generation_kwargs().
    """
    return "simplify: "


def build_generation_kwargs(config: SimplifierConfig, base_max_new_tokens: int) -> dict:
    """Параметры для model.generate(), отражающие конфиг, БЕЗ изменения
    текста промпта (в отличие от старого build_prefix).

    - max_length_ratio масштабирует max_new_tokens: модель не сможет
      выдать ответ длиннее, чем разрешено конфигом.
    - aggressiveness влияет на repetition_penalty/length_penalty: более
      агрессивное упрощение сильнее штрафует копирование входа один в
      один и немного поощряет более короткие формулировки.

    Это обычные, поддерживаемые transformers параметры generate(), а не
    текст, который модель должна "понять" сама.
    """
    max_new_tokens = max(8, round(base_max_new_tokens * config.max_length_ratio))

    repetition_penalty = {
        "conservative": 1.0,
        "balanced": 1.2,
        "aggressive": 1.4,
    }.get(config.aggressiveness, 1.0)

    length_penalty = {
        "conservative": 1.0,
        "balanced": 0.9,
        "aggressive": 0.7,
    }.get(config.aggressiveness, 1.0)

    return {
        "max_new_tokens": max_new_tokens,
        "repetition_penalty": repetition_penalty,
        "length_penalty": length_penalty,
    }
