"""
markdown_formatter.py — детерминированное, чисто скриптованное
преобразование упрощённого узбекского текста в структурированный Markdown.

ВАЖНО: здесь НЕТ вызовов модели, API или каких-либо ML-компонентов.
Вся логика — обычные функции на regex/str, работающие как
чистая функция строка -> строка: to_markdown(text) всегда возвращает
один и тот же результат для одного и того же входа.

Используется в app.py как обработчик кнопки "В Markdown".
"""

from __future__ import annotations

import re
from typing import List, Optional

# ---------------------------------------------------------------------------
# 1. Словарь топ-20 юридических терминов (по частоте в data/domain_legal.csv,
#    колонка text_simplified). Статический список — НЕ модель, просто
#    таблица "стем -> есть ли термин".
# ---------------------------------------------------------------------------
#
# Порядок важен только для читаемости — при сопоставлении термины всегда
# сортируются по убыванию длины стема, чтобы "qonunchilik" не "съедался"
# более коротким "qonun" (см. _build_term_pattern).

LEGAL_TERMS: List[str] = [
    "qonunchilik",   # законодательство
    "respublika",    # республика
    "fuqarolik",     # гражданское (право)
    "maʼmuriy",       # административный
    "harbiy",        # военный
    "qonun",         # закон
    "davlat",        # государство
    "xodim",         # работник/сотрудник
    "mehnat",        # труд
    "huquq",         # право
    "shaxs",         # лицо
    "sudya",         # судья
    "hujjat",        # документ
    "shart",         # условие
    "himoya",        # защита
    "aliment",       # алименты
    "bekor",         # отмена/аннулирование
    "rais",          # председатель
    "organ",         # орган
    "zarar",         # ущерб/вред
]

# Символы, которые узбекская латиница использует внутри слова
# (включая варианты апострофа для oʻ/gʻ/tutuq belgisi): ʻ ʼ ʺ '
_UZ_WORD_CHARS = "A-Za-zʻʼʺ'"


def _build_term_pattern() -> re.Pattern:
    """
    Собирает один общий regex для всех терминов словаря.

    Каждый термин ищется как НАЧАЛО слова (word boundary слева) с
    произвольным агглютинативным продолжением справа (суффиксы падежей,
    принадлежности, множественного числа и т.д.), а не как точная
    подстрока — это и есть требование "не ломать словоформы".

    Термины отсортированы по убыванию длины стема, чтобы при
    альтернации regex более длинный термин (например "qonunchilik")
    матчился раньше более короткого-префикса ("qonun").
    """
    terms_sorted = sorted(LEGAL_TERMS, key=len, reverse=True)
    alternation = "|".join(re.escape(t) for t in terms_sorted)
    pattern = (
        rf"(?<![{_UZ_WORD_CHARS}])"
        rf"(?:{alternation})"
        rf"[{_UZ_WORD_CHARS}]*"
        rf"(?![{_UZ_WORD_CHARS}])"
    )
    return re.compile(pattern, flags=re.IGNORECASE)


_TERM_PATTERN = _build_term_pattern()


def bold_legal_terms(text: str) -> str:
    """Оборачивает найденные словоформы юридических терминов в **bold**."""
    if not text:
        return text

    def _wrap(match: re.Match) -> str:
        word = match.group(0)
        return f"**{word}**"

    return _TERM_PATTERN.sub(_wrap, text)


# ---------------------------------------------------------------------------
# 2. Разбиение на смысловые блоки (абзацы)
# ---------------------------------------------------------------------------

# Простое эвристическое разбиение предложений (по точке/!/?, с учётом
# сокращений с точкой внутри номера пункта типа "1." — они обрабатываются
# отдельно в _extract_numbered_items, поэтому здесь не критично).
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[A-Za-zʻʼʺ'][.!?])\s+(?=[A-ZʼʻʺA-Za-zʻʼʺ0-9])"
)

# Максимум предложений в одном "искусственном" абзаце, если в тексте
# вообще нет двойных переносов строк (эвристика длины).
_MAX_SENTENCES_PER_BLOCK = 3


def split_into_paragraphs(text: str) -> List[str]:
    """
    Делит текст на абзацы.

    1) Если есть явные разделители \n\n — используем их.
    2) Иначе используем эвристику: разбиваем на предложения и группируем
       их по _MAX_SENTENCES_PER_BLOCK штук в один блок.
    """
    if not text.strip():
        return []

    if "\n\n" in text:
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        return blocks

    # Одиночные переносы строк тоже считаем разделителями абзацев,
    # если их несколько (например список, вставленный построчно).
    if "\n" in text.strip():
        blocks = [b.strip() for b in text.split("\n") if b.strip()]
        if len(blocks) > 1:
            return blocks

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    if not sentences:
        return [text.strip()]

    blocks = []
    for i in range(0, len(sentences), _MAX_SENTENCES_PER_BLOCK):
        chunk = " ".join(sentences[i : i + _MAX_SENTENCES_PER_BLOCK])
        blocks.append(chunk)
    return blocks


# ---------------------------------------------------------------------------
# 3. Нумерованные списки: "1.", "2)", "3 -" и т.п.
# ---------------------------------------------------------------------------

_NUMBERED_ITEM_RE = re.compile(
    r"(?:(?<=^)|(?<=\s))(\d{1,2})[.)]\s+"
)


def _extract_numbered_items(paragraph: str) -> Optional[List[str]]:
    """
    Если в абзаце встречаются маркеры вида "1. ...", "2) ..." — режем
    его на пункты нумерованного списка. Возвращает None, если таких
    маркеров нет (тогда абзац обрабатывается как обычный текст).
    """
    matches = list(_NUMBERED_ITEM_RE.finditer(paragraph))
    if len(matches) < 1:
        return None

    items = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(paragraph)
        item_text = paragraph[start:end].strip().rstrip(";,")
        if item_text:
            items.append(item_text)

    # Если нумерация начинается не с самого начала абзаца, текст перед
    # первым маркером сохраняем как отдельную вводную строку.
    prefix = paragraph[: matches[0].start()].strip()
    if prefix:
        items.insert(0, "__PREFIX__" + prefix)

    return items if items else None


# ---------------------------------------------------------------------------
# 4. Маркированные списки: перечисления через "во-первых / также / кроме
#    того / shuningdek / bundan tashqari" и однородные пункты через запятую.
# ---------------------------------------------------------------------------

# Маркеры перечисления (RU + UZ, т.к. итоговый упрощённый текст — узбекский,
# но модуль может получить и русский текст).
_ENUM_MARKERS = [
    r"во[-\s]первых",
    r"во[-\s]вторых",
    r"в[-\s]третьих",
    r"кроме\s+того",
    r"также",
    r"birinchidan",
    r"ikkinchidan",
    r"uchinchidan",
    r"shuningdek",
    r"bundan\s+tashqari",
    r"shu\s+bilan\s+birga",
]
_ENUM_MARKER_RE = re.compile(
    r"\b(?:" + "|".join(_ENUM_MARKERS) + r")\b",
    flags=re.IGNORECASE,
)

# Однородные пункты через запятую: минимум 3 элемента, разделённых
# запятой, без союзов внутри — типичный шаблон перечисления в юртексте.
_COMMA_LIST_RE = re.compile(
    r"^[^,:;]{1,60}(?:,\s*[^,:;]{1,60}){2,}\.?$"
)

# Та же форма, но с вводной фразой до двоеточия перед списком —
# "Bu huquqlarga quyidagilar kiradi: A, B, C." Вводная часть остаётся
# отдельной строкой (__PREFIX__), а сам список парсится после ":".
_COMMA_LIST_WITH_INTRO_RE = re.compile(
    r"^(?P<intro>[^:]{1,120}):\s*"
    r"(?P<items>[^,:;]{1,60}(?:,\s*[^,:;]{1,60}){2,}\.?)$"
)


def _split_enum_markers(paragraph: str) -> Optional[List[str]]:
    """Разбивает абзац на пункты по маркерам "также/кроме того/shuningdek" и т.п."""
    positions = [m.start() for m in _ENUM_MARKER_RE.finditer(paragraph)]
    if len(positions) < 2:
        return None

    items = []
    prefix = paragraph[: positions[0]].strip()
    if prefix:
        items.append("__PREFIX__" + prefix)

    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(paragraph)
        chunk = paragraph[start:end].strip().rstrip(";,")
        if chunk:
            items.append(chunk)
    return items if len(items) >= 2 else None


def _split_comma_enumeration(paragraph: str) -> Optional[List[str]]:
    """
    Если весь абзац — это перечисление однородных пунктов через запятую,
    либо вводная фраза + двоеточие + такое перечисление.
    """
    stripped = paragraph.strip()

    intro_match = _COMMA_LIST_WITH_INTRO_RE.match(stripped)
    if intro_match:
        intro = intro_match.group("intro").strip()
        items_str = intro_match.group("items")
        parts = [p.strip().rstrip(".") for p in items_str.rstrip(".").split(",")]
        parts = [p for p in parts if p]
        if len(parts) < 3:
            return None
        return ["__PREFIX__" + intro + ":"] + parts

    if not _COMMA_LIST_RE.match(stripped):
        return None
    parts = [p.strip().rstrip(".") for p in stripped.rstrip(".").split(",")]
    parts = [p for p in parts if p]
    if len(parts) < 3:
        return None
    return parts


# ---------------------------------------------------------------------------
# 5. Сборка одного абзаца в Markdown-блок
# ---------------------------------------------------------------------------


def _render_list(items: List[str], numbered: bool) -> str:
    lines = []
    counter = 1
    for item in items:
        if item.startswith("__PREFIX__"):
            lines.append(bold_legal_terms(item[len("__PREFIX__"):]))
            continue
        marker = f"{counter}. " if numbered else "- "
        lines.append(marker + bold_legal_terms(item))
        if numbered:
            counter += 1
    return "\n".join(lines)


def _format_paragraph(paragraph: str) -> str:
    paragraph = paragraph.strip()
    if not paragraph:
        return ""

    numbered_items = _extract_numbered_items(paragraph)
    if numbered_items:
        return _render_list(numbered_items, numbered=True)

    marker_items = _split_enum_markers(paragraph)
    if marker_items:
        return _render_list(marker_items, numbered=False)

    comma_items = _split_comma_enumeration(paragraph)
    if comma_items:
        return _render_list(comma_items, numbered=False)

    return bold_legal_terms(paragraph)


# ---------------------------------------------------------------------------
# 6. Публичная функция
# ---------------------------------------------------------------------------


def to_markdown(text: str) -> str:
    """
    Детерминированно преобразует упрощённый узбекский текст в
    структурированный Markdown: абзацы, маркированные/нумерованные
    списки и **выделение** частотных юридических терминов.

    Никаких вызовов модели/API — только regex и словарный поиск.
    """
    if text is None:
        return ""
    text = text.strip()
    if not text:
        return ""

    paragraphs = split_into_paragraphs(text)
    blocks = [_format_paragraph(p) for p in paragraphs]
    blocks = [b for b in blocks if b]
    return "\n\n".join(blocks)
