"""
A/B-сравнение эффекта параметров SimplifierConfig на генерацию.

Прогоняет список тестовых текстов через уже существующий инференс
(app.app.ModelRunner) с 2-3 конфигами и пишет результат в JSON для
РУЧНОГО сравнения. Никаких метрик/скоринга внутри скрипта нет —
эффект prompt-инжиниринга на mT5-small, не fine-tuned под такую
разметку, не гарантирован и оценивается человеком (см. docstring в
app/config.py).

Запуск из корня репозитория:
    python scripts/ab_compare.py
"""

from __future__ import annotations

import json
import os
import sys

# app/ не является пакетом (нет __init__.py) — переиспользуем его код,
# добавляя app/ в sys.path, а не дублируя ModelRunner/build_prefix.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_DIR = os.path.join(_REPO_ROOT, "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from app import ModelRunner  # noqa: E402  (существующий инференс, не дублируем)
from config import SimplifierConfig, build_prefix  # noqa: E402

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ab_compare_output.json")

# Тестовые тексты для сравнения. Можно заменить/расширить.
TEST_TEXTS = [
    "Oʻzbekiston Respublikasi Vazirlar Mahkamasining qarori bilan tasdiqlangan "
    "Nizomga muvofiq, davlat organlari oʻz vakolatlari doirasida tegishli "
    "chora-tadbirlarni amalga oshiradilar.",
    "Ushbu shartnoma tomonlar oʻrtasida oʻzaro kelishilgan holda tuzilgan boʻlib, "
    "unga muvofiq har bir tomon oʻz majburiyatlarini belgilangan muddatlarda "
    "bajarishi shart.",
]

# 2-3 конфига для сравнения: default (=прежнее поведение) vs все drop_*=True.
CONFIGS = {
    "default": SimplifierConfig(),
    "all_drops": SimplifierConfig(drop_dates=True, drop_law_refs=True, drop_stats=True),
    "aggressive": SimplifierConfig(aggressiveness="aggressive"),
}


def main():
    runner = ModelRunner()
    print("Loading model (this can take a while on first run)...")
    runner._load()  # синхронная загрузка, без потока — это одноразовый CLI-скрипт
    if not runner.loaded:
        print("Model failed to load, aborting.")
        return

    results = []
    for text in TEST_TEXTS:
        for config_name, config in CONFIGS.items():
            prefix = build_prefix(config)
            print(f"Running: config={config_name!r} prefix={prefix!r}")
            output = runner.simplify(text, prefix=prefix)
            results.append({
                "input_text": text,
                "config_name": config_name,
                "config": {
                    "aggressiveness": config.aggressiveness,
                    "max_length_ratio": config.max_length_ratio,
                    "drop_dates": config.drop_dates,
                    "drop_law_refs": config.drop_law_refs,
                    "drop_stats": config.drop_stats,
                },
                "prefix": prefix,
                "output_text": output,
            })

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(results)} results to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
