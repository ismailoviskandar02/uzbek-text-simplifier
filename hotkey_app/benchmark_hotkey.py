"""
Замер времени отклика для global-hotkey фичи.

Раздельно измеряет:
  1. clipboard_capture — накладные расходы новой системной обвязки
     (эмуляция Ctrl+C + poll буфера обмена, см. global_hotkey.py).
  2. inference — время УЖЕ существующего ModelRunner.simplify() на тех же
     текстах, которое воспроизводимо и без хоткея (неизменно, не часть
     новой фичи).

Позволяет ответить на вопрос "сколько задержки добавляет новая системная
интеграция поверх неизменного времени инференса" — не запуская реальный
глобальный хоткей руками 5-10 раз.

Запуск:
    python benchmark_hotkey.py

Требует установленных зависимостей из requirements.txt (torch,
transformers — для инференса; pyperclip — для замера буфера).
Скачивание весов модели с Hugging Face Hub требует сетевого доступа.
"""

from __future__ import annotations

import statistics
import time

from app_tkinter import ModelRunner  # переиспользуем существующий инференс как есть

SAMPLE_TEXTS = [
    "Ariza.",
    "Ushbu qaror 2024-yil 1-yanvardan kuchga kiradi.",
    (
        "Fuqarolarning murojaatlari toʻgʻrisidagi qonun hujjatlariga muvofiq, "
        "davlat organlari fuqarolar murojaatlarini koʻrib chiqishi shart."
    ),
    (
        "Ushbu Nizom Oʻzbekiston Respublikasi Konstitutsiyasi, qonunlari va "
        "boshqa normativ-huquqiy hujjatlariga muvofiq ishlab chiqilgan boʻlib, "
        "tashkilotning huquqiy maqomi, vazifalari va faoliyat tartibini belgilaydi."
    ),
    (
        "Mazkur shartnoma tomonlar oʻrtasida yuzaga keladigan barcha huquq va "
        "majburiyatlarni, shu jumladan moliyaviy hisob-kitoblar, mulkiy "
        "javobgarlik va nizolarni hal etish tartibini belgilaydi hamda uning "
        "amal qilish muddati bir yilni tashkil etadi."
    ),
    (
        "2023-yil davomida mintaqada amalga oshirilgan ijtimoiy-iqtisodiy "
        "islohotlar natijasida aholi turmush darajasi sezilarli darajada "
        "oshdi, ish oʻrinlari soni koʻpaydi va tadbirkorlik faoliyati uchun "
        "qulay shart-sharoitlar yaratildi, bu esa oʻz navbatida mahalliy "
        "byudjet daromadlarining barqaror oʻsishiga olib keldi."
    ),
]

# Ещё пара более длинных текстов для полноты диапазона (до 5-10 образцов).
SAMPLE_TEXTS += [
    (
        "Ushbu bitim asosida taraflar oʻzaro hamkorlik qilishga, axborot "
        "almashishga va zarur boʻlgan barcha rasmiy hujjatlarni belgilangan "
        "muddatlarda taqdim etishga majburdirlar, aks holda javobgarlik "
        "amaldagi qonun hujjatlarida nazarda tutilgan tartibda belgilanadi."
    ),
    (
        "Sud qarori qonuniy kuchga kirgandan soʻng oʻn kun ichida ijro "
        "etilishi lozim, aks holda ijro hujjatlari ijro byurosiga topshiriladi "
        "va majburiy ijro choralari koʻriladi, bu esa qoʻshimcha xarajatlar "
        "va sanksiyalarni keltirib chiqarishi mumkin."
    ),
]


def _simulate_clipboard_capture_overhead(poll_interval: float = 0.03) -> float:
    """Изолированный замер накладных расходов эмуляции Ctrl+C + poll буфера,
    без реального системного хоткея (используется тот же капчер, но с
    предзаполненным буфером, чтобы тест был воспроизводим без ручного
    выделения текста)."""
    import global_hotkey

    if global_hotkey.backend_name() is None:
        raise RuntimeError(
            "Нет доступного backend'а (keyboard/pynput) в этом окружении — "
            "замер clipboard_capture пропущен. Запустите на десктопе с "
            "установленным keyboard/pynput и правами (см. README)."
        )

    import pyperclip

    pyperclip.copy("placeholder")
    t0 = time.monotonic()
    _ = global_hotkey.capture_selected_text(timeout=1.0, poll_interval=poll_interval)
    return time.monotonic() - t0


def main() -> None:
    print(f"Loading model (this happens once, same as app startup)...")
    runner = ModelRunner()
    runner._load()  # синхронно, чтобы не ждать колбэк в этом скрипте
    if not runner.loaded:
        print("Model failed to load — aborting benchmark.")
        return

    inference_times = []
    print(f"\n{'len(chars)':>10}  {'inference_ms':>13}")
    print("-" * 26)
    for text in SAMPLE_TEXTS:
        t0 = time.monotonic()
        runner.simplify(text, num_beams=4)
        elapsed_ms = (time.monotonic() - t0) * 1000
        inference_times.append(elapsed_ms)
        print(f"{len(text):>10}  {elapsed_ms:>13.1f}")

    print("-" * 26)
    print(f"inference mean={statistics.mean(inference_times):.1f}ms "
          f"median={statistics.median(inference_times):.1f}ms "
          f"min={min(inference_times):.1f}ms max={max(inference_times):.1f}ms")

    print("\nMeasuring clipboard-capture overhead (new system glue)...")
    try:
        clipboard_ms = _simulate_clipboard_capture_overhead() * 1000
        print(f"clipboard_capture ≈ {clipboard_ms:.1f}ms (single sample)")
        print(
            f"\nEstimated total hotkey->result latency ≈ "
            f"{clipboard_ms + statistics.median(inference_times):.1f}ms "
            f"(clipboard_capture + median inference)"
        )
    except RuntimeError as e:
        print(f"Skipped: {e}")


if __name__ == "__main__":
    main()
