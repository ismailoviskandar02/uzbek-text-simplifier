"""
Кросс-платформенный launcher для Uzbek Text Simplifier.

Зачем он нужен: глобальный хоткей (см. global_hotkey.py) на разных ОС
требует разных прав:
  - Windows: библиотека `keyboard` иногда не может зарегистрировать
    хоткей / эмулировать Ctrl+C без прав администратора.
  - Linux: `keyboard` читает /dev/input напрямую и обычно требует root;
    без root приложение само переключится на `pynput` (не требует root,
    но нужен X11) — в таком случае просто предупредим и запустим как есть.
  - macOS: элевация через sudo тут не помогает, права выдаются в
    System Settings -> Privacy & Security (Accessibility / Input
    Monitoring) — просто предупреждаем.

Использование (одинаково на любой ОС):
    python run.py

Скрипт сам определяет ОС, при необходимости перезапускает себя с
повышенными правами (запросит подтверждение UAC на Windows), а затем
стартует app_tkinter.py в этом же процессе.

Никакой "магии": если элевация не нужна или не поддерживается на данной
ОС, скрипт просто запускает приложение как обычно — глобальный хоткей
в этом случае может не зарегистрироваться, но приложение само покажет
понятную ошибку (см. global_hotkey.py), а не упадёт молча.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ENTRY = os.path.join(APP_DIR, "app_tkinter.py")
LOG_FILE = os.path.join(APP_DIR, "run_debug.log")

_ELEVATED_FLAG = "--elevated"  # маркер, чтобы не зациклить повторную элевацию

_log_fp = None  # инициализируется в main(), пишем и в консоль, и в файл


def _log(message: str) -> None:
    """Печатает в консоль И дописывает в run_debug.log — если окно
    консоли исчезнет (например, при краше elevated-процесса), лог всё
    равно останется на диске и его можно будет прислать для диагностики."""
    print(message)
    global _log_fp
    try:
        if _log_fp is None:
            _log_fp = open(LOG_FILE, "a", encoding="utf-8")
        _log_fp.write(message + "\n")
        _log_fp.flush()
    except Exception:  # noqa: BLE001
        pass


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_linux() -> bool:
    return platform.system() == "Linux"


# ---------------------------------------------------------------------------
# Windows: проверка прав администратора + перезапуск через UAC при необходимости
# ---------------------------------------------------------------------------

def _windows_is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _windows_relaunch_as_admin() -> bool:
    """Запрашивает у пользователя подтверждение UAC и перезапускает скрипт
    с правами администратора В НОВОМ ОКНЕ КОНСОЛИ, которое остаётся
    открытым (через cmd /k) — иначе при ошибке окно мгновенно закрывается
    и пользователь не успевает увидеть, что пошло не так.

    Возвращает True, если перезапуск инициирован (текущий процесс должен
    завершиться)."""
    try:
        import ctypes

        script_path = os.path.abspath(__file__)
        # cmd /k держит окно открытым даже после завершения/падения python —
        # без этого при ошибке окно просто мигает и исчезает.
        inner_cmd = " ".join(
            f'"{a}"' for a in ([sys.executable, script_path, _ELEVATED_FLAG] + sys.argv[1:])
        )
        params = f'/k {inner_cmd}'
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "cmd.exe", params, APP_DIR, 1
        )
        # ShellExecuteW возвращает значение > 32 при успехе
        return int(ret) > 32
    except Exception as e:  # noqa: BLE001
        _log(f"[run.py] Не удалось запросить права администратора: {e}")
        return False


def _handle_windows() -> None:
    if _ELEVATED_FLAG in sys.argv:
        _log("[run.py] Запущено с правами администратора.")
        return

    if _windows_is_admin():
        _log("[run.py] Уже запущено с правами администратора.")
        return

    _log(
        "[run.py] Приложение использует библиотеку 'keyboard' для глобального "
        "хоткея — на Windows это иногда требует прав администратора. "
        "Запрашиваю повышение прав (появится диалог UAC)..."
    )
    if _windows_relaunch_as_admin():
        # Дочерний elevated-процесс запущен — текущий (неэлевированный) выходим.
        sys.exit(0)
    else:
        _log(
            "[run.py] Повышение прав не удалось/отменено пользователем — "
            "продолжаю запуск БЕЗ прав администратора. Если хоткей не "
            "зарегистрируется, приложение покажет соответствующую ошибку; "
            "просто перезапустите run.py и подтвердите UAC-запрос."
        )


# ---------------------------------------------------------------------------
# Linux: проверка root, мягкое предупреждение без насильственной элевации
# (GUI-приложения как root — плохая практика: ломает $DISPLAY/$XAUTHORITY,
# поэтому НЕ делаем автоматический sudo-релонч, а просто подсказываем).
# ---------------------------------------------------------------------------

def _handle_linux() -> None:
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if is_root:
        _log("[run.py] Запущено от root — backend 'keyboard' должен работать.")
        return

    _log(
        "[run.py] Запущено без root. Библиотека 'keyboard' обычно требует root "
        "для чтения /dev/input напрямую — если она недоступна/не даёт прав, "
        "приложение автоматически переключится на backend 'pynput' (работает "
        "через X11, root не нужен, но нужен запущенный X-сервер).\n"
        "[run.py] Если хотите принудительно использовать 'keyboard': "
        "запустите `sudo -E python3 run.py` (флаг -E сохраняет $DISPLAY для GUI)."
    )


# ---------------------------------------------------------------------------
# macOS: элевация через sudo бесполезна — права выдаются через системные настройки
# ---------------------------------------------------------------------------

def _handle_macos() -> None:
    _log(
        "[run.py] macOS: для глобального хоткея библиотеке 'pynput' нужно выдать "
        "разрешения в System Settings -> Privacy & Security -> Accessibility "
        "и Input Monitoring для приложения/терминала, из которого запускается "
        "run.py. sudo здесь не поможет и не запрашивается."
    )


# ---------------------------------------------------------------------------
# Общая проверка зависимостей (не ставит их автоматически — только предупреждает)
# ---------------------------------------------------------------------------

def _check_requirements() -> None:
    missing = []
    for module_name in ("pyperclip", "pystray", "PIL"):
        try:
            __import__(module_name)
        except Exception:  # noqa: BLE001 - pystray может кидать не ImportError,
            # а ValueError при выборе backend'а на нестандартном окружении
            missing.append(module_name)

    has_hotkey_backend = False
    for module_name in ("keyboard", "pynput"):
        try:
            __import__(module_name)
            has_hotkey_backend = True
            break
        except Exception:  # noqa: BLE001
            continue

    if not has_hotkey_backend:
        missing.append("keyboard или pynput")

    if missing:
        _log(
            "[run.py] ВНИМАНИЕ: не найдены зависимости: "
            f"{', '.join(missing)}.\n"
            "[run.py] Установите их командой:\n"
            f"    {sys.executable} -m pip install -r "
            f"{os.path.join(APP_DIR, 'requirements.txt')}\n"
            "[run.py] Приложение всё равно попробует запуститься — часть "
            "функций (глобальный хоткей / трей) может быть недоступна."
        )


def main() -> None:
    system = platform.system()

    if _is_windows():
        _handle_windows()
    elif _is_linux():
        _handle_linux()
    elif _is_macos():
        _handle_macos()
    else:
        _log(f"[run.py] Неизвестная ОС '{system}' — запускаю без специальной подготовки.")

    _check_requirements()

    _log(f"[run.py] Запуск приложения из {APP_ENTRY} ...")
    _log(f"[run.py] Полный лог этого запуска также сохраняется в: {LOG_FILE}")

    # Запускаем app_tkinter.py в этом же процессе (чтобы окно консоли не
    # плодило дочерние процессы без необходимости).
    sys.path.insert(0, APP_DIR)
    os.chdir(APP_DIR)

    import runpy

    runpy.run_path(APP_ENTRY, run_name="__main__")


if __name__ == "__main__":
    try:
        main()
        _log("[run.py] Приложение завершилось штатно (окно закрыто через 'Выход' в трее).")
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        # Если тут не поймать исключение, окно консоли на Windows мгновенно
        # закроется и пользователь не увидит текст ошибки. Печатаем полный
        # traceback, пишем в лог-файл и ждём Enter перед закрытием.
        _log("[run.py] ПРОИЗОШЛА ОШИБКА при запуске приложения:")
        _log(traceback.format_exc())
        if _is_windows():
            input("\nНажмите Enter, чтобы закрыть это окно...")
        sys.exit(1)
