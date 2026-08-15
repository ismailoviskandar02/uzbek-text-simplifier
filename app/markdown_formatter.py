"""
markdown_formatter.py — детерминированное, чисто скриптованное
преобразование упрощённого узбекского текста в структурированный Markdown.

ВАЖНО: здесь НЕТ вызовов модели, API или каких-либо ML-компонентов.
Вся логика — обычные функции на regex/str, работающие как
чистая функция строка -> строка: to_markdown(text) всегда возвращает
один и тот же результат для одного и того же входа. Никакой сети,
файловой системы (кроме статического словаря терминов, объявленного
как константа модуля) и внешнего состояния.

Используется в app.py как обработчик кнопки "В Markdown".

Порядок применения правил (задокументирован явно, важен):
    0. Проверка идемпотентности (уже отформатированный markdown -> без изменений)
    1. Заголовок документа (H1)
    2. Разбиение на абзацы (короче, чем раньше — для лучшей читаемости)
    3. Заголовки разделов/статей (##, ###) внутри абзацев
    4. Предупреждения/примечания -> blockquote с ⚠️
    5. Определения/цитаты -> blockquote
    6. Списки: нумерованные, маркированные, вложенные a)/b),
       альтернативы через "или"/"yoki"/"либо"
    7. Ссылки на статьи закона -> inline code
    8. Числа: даты, проценты, суммы -> inline code (не теряются в тексте)
    9. Аббревиатуры -> расшифровка при первом упоминании
    10. Термины -> **bold** при первом употреблении в абзаце
    11. Таблицы (регулярная структура "X — Y" x3+)
    12. Горизонтальные разделители между top-level разделами
    13. Финальная сборка блоков с усиленными пустыми строками
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. Словарь топ-20 юридических терминов (латиница + кириллица — реальный
#    вывод модели кириллический, но модуль должен работать на обоих).
#    Статический список — НЕ модель, просто таблица "стем -> термин".
# ---------------------------------------------------------------------------

LEGAL_TERMS: List[str] = [
    # -- лотин ёзуви (Latin script) --
    "qonunchilik", "respublika", "fuqarolik", "maʼmuriy", "harbiy",
    "qonun", "davlat", "xodim", "mehnat", "huquq", "shaxs", "sudya",
    "hujjat", "shart", "himoya", "aliment", "bekor", "rais", "organ",
    "zarar",
    # -- кирилл ёзуви (Cyrillic script) — те же 20 терминов, т.к. модель
    #    и приложение реально выводят упрощённый текст в кириллице.
    "қонунчилик", "республика", "фуқаролик", "маъмурий", "ҳарбий",
    "қонун", "давлат", "ходим", "меҳнат", "ҳуқуқ", "шахс", "судья",
    "ҳужжат", "шарт", "ҳимоя", "алимент", "бекор", "раис", "орган",
    "зарар",
]

# Известные аббревиатуры узбекского законодательства -> расшифровка.
# Статический словарь: если аббревиатуры нет здесь, мы её не выдумываем.
# ВАЖНО: каждая аббревиатура дублируется в обоих алфавитах — реальный
# текст может быть либо кириллическим, либо латинским узбекским, и
# сокращения кодексов пишутся по-разному в каждом письме.
ABBREVIATIONS = {
    # -- кирилл ёзуви --
    "ЖК": "Жиноят кодекси",
    "ФК": "Фуқаролик кодекси",
    "МК": "Маъмурий кодекс",
    "МЖ": "Меҳнат кодекси",
    "ИЖК": "Иқтисодий жиноятлар кодекси",
    # -- лотин ёзуви (те же кодексы, латинская запись) --
    "JK": "Jinoyat kodeksi",
    "FK": "Fuqarolik kodeksi",
    "MK": "Maʼmuriy kodeks",
    "MJ": "Mehnat kodeksi",
    "IJK": "Iqtisodiy jinoyatlar kodeksi",
}

# Символы, которые узбекский текст использует внутри слова — и латиница,
# и кириллица (включая узбекские буквы ў/қ/ғ/ҳ и варианты апострофа
# для oʻ/gʻ/tutuq belgisi): ʻ ʼ ʺ '
_UZ_WORD_CHARS = "A-Za-zʻʼʺ'А-Яа-яЁёЎўҚқҒғҲҳ"


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


def bold_legal_terms(text: str, seen: Optional[set] = None) -> str:
    """
    Оборачивает найденные словоформы юридических терминов в **bold**.

    `seen`, если передан, — множество уже выделенных (в нижнем регистре)
    словоформ термина в текущем абзаце: повторные вхождения того же
    термина дальше по тексту не дублируются жирным. Сбрасывается на
    каждый новый абзац вызывающей стороной (_format_paragraph создаёт
    новое множество на каждый абзац) — т.е. в одном абзаце термин
    выделяется один раз, но в следующем абзаце снова один раз.
    """
    if not text:
        return text
    if seen is None:
        seen = set()

    def _wrap(match: re.Match) -> str:
        word = match.group(0)
        key = word.lower()
        if key in seen:
            return word
        seen.add(key)
        return f"**{word}**"

    return _TERM_PATTERN.sub(_wrap, text)


# ---------------------------------------------------------------------------
# 2. Идемпотентность: если текст уже похож на результат to_markdown(),
#    возвращаем его без изменений. Выбран вариант (b) из ТЗ: функция сама
#    детектирует "уже markdown" и не форматирует повторно — так кнопка
#    безопасна независимо от того, что именно хранит вызывающий код.
# ---------------------------------------------------------------------------

_ALREADY_MARKDOWN_RE = re.compile(
    r"(^#{1,3}\s+\S)"          # заголовок в начале строки
    r"|(^\s*[-*]\s+\S)"        # маркированный список
    r"|(^\s*\d+\.\s+\S)"       # нумерованный список
    r"|(^\s*>\s*\S)"           # blockquote
    r"|(\*\*[^*\n]+\*\*)"      # жирный текст
    r"|(^\s*\|.+\|\s*$)"       # строка таблицы
    r"|(^\s*---\s*$)",         # горизонтальный разделитель
    flags=re.MULTILINE,
)


def _looks_like_markdown(text: str) -> bool:
    """Грубая, но детерминированная эвристика: текст уже содержит
    характерные markdown-конструкции хотя бы в двух независимых местах —
    значит, скорее всего, он уже прошёл через to_markdown()."""
    hits = len(_ALREADY_MARKDOWN_RE.findall(text))
    return hits >= 2


# ---------------------------------------------------------------------------
# 3. Заголовок документа (H1) и заголовки разделов/статей (H2/H3)
# ---------------------------------------------------------------------------

_TITLE_LINE_RE = re.compile(
    r"^[\"«]?[A-ZʼʻʺА-ЯЁЎҚҒҲ][^.!?]{3,79}[\"»]?$"
)

_CHAPTER_RE = re.compile(
    r"^(глава|раздел|bob|bo['ʻʼ]lim|боб|бўлим)\s+(\d+)\.?\s*(.*)$",
    flags=re.IGNORECASE,
)

# Узбекский (и латиница, и кириллица) чаще пишет номер ПЕРЕД словом:
# "2-bob", "3-bo'lim", "2-боб", "3-бўлим" — в отличие от русского
# порядка "Глава 2" / "Раздел 3", который ловит regex выше.
_CHAPTER_NUM_FIRST_RE = re.compile(
    r"^(\d+)[-\s](bob|bo['ʻʼ]lim|боб|бўлим)\.?\s*(.*)$",
    flags=re.IGNORECASE,
)

_ARTICLE_HEADER_RE = re.compile(
    r"^(статья\s+(\d+)|(\d+)[-\s]?модда|(\d+)[-\s]?modda)\.?\s*(.*)$",
    flags=re.IGNORECASE,
)


def extract_title(text: str) -> Tuple[Optional[str], str]:
    """
    Если первая строка текста похожа на заголовок документа (короткая,
    без завершающей точки, начинается с заглавной буквы) и после неё
    ещё есть текст — выносим её как `# Заголовок`, возвращаем остаток.
    """
    if "\n" not in text:
        return None, text

    parts = text.split("\n", 1)
    if len(parts) != 2:
        return None, text
    first, rest = parts[0].strip(), parts[1]
    if not rest.strip():
        return None, text
    if (
        _TITLE_LINE_RE.match(first)
        and len(first.split()) <= 12
        and not _CHAPTER_RE.match(first)
        and not _CHAPTER_NUM_FIRST_RE.match(first)
        and not _ARTICLE_HEADER_RE.match(first)
        and not _TABLE_ROW_RE.match(first)
    ):
        return first.strip("\"«»"), rest
    return None, text


def _format_section_header(paragraph: str) -> Optional[str]:
    """Детект 'Глава N ...' / 'Раздел N ...' (слово-номер, обычно
    в кириллице/русской кальке) и 'N-bob ...' / 'N-bo'lim ...'
    (номер-слово, стандартный узбекский порядок и в латинице,
    и в кириллице) -> ## заголовок."""
    stripped = paragraph.strip()

    m = _CHAPTER_RE.match(stripped)
    if m:
        kind, num, rest = m.group(1), m.group(2), m.group(3).strip()
        label = kind.capitalize()
        title = f"## {label} {num}"
        if rest:
            title += f". {rest}"
        return title

    m = _CHAPTER_NUM_FIRST_RE.match(stripped)
    if m:
        num, kind, rest = m.group(1), m.group(2), m.group(3).strip()
        label = kind.capitalize()
        title = f"## {num}-{label}"
        if rest:
            title += f". {rest}"
        return title

    return None


def _format_article_header(paragraph: str) -> Optional[str]:
    """
    Детект 'Статья N ...' / 'N-modda ...' как начало смыслового блока.
    Если за заголовком следует несколько предложений — это заголовок
    раздела (### Статья N). Если это короткий одиночный пункт — не
    трогаем (вернём None, обработается как обычный текст/пункт списка).
    """
    m = _ARTICLE_HEADER_RE.match(paragraph.strip())
    if not m:
        return None
    num = m.group(2) or m.group(3) or m.group(4)
    rest = m.group(5).strip()
    # Если статья встречается как один короткий пункт внутри перечисления
    # (в паре с другими "N." маркерами в том же абзаце) — не заголовок,
    # это обрабатывается списковой логикой ниже по pipeline.
    if _NUMBERED_ITEM_RE.search(paragraph[m.end(1) :]):
        return None
    title = f"### Статья {num}"
    if rest:
        return title + "\n\n" + rest
    return title


# ---------------------------------------------------------------------------
# 4. Разбиение на смысловые блоки (абзацы)
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[A-Za-zʻʼʺ'А-Яа-яЁёЎўҚқҒғҲҳ][.!?])\s+"
    r"(?=[A-ZʼʻʺА-ЯЁЎҚҒҲA-Za-zʻʼʺ'А-Яа-яЁёЎўҚқҒғҲҳ0-9])"
)

_MAX_SENTENCES_PER_BLOCK = 2  # короче блоки -> больше "воздуха" между ними


def split_into_paragraphs(text: str) -> List[str]:
    if not text.strip():
        return []

    if "\n\n" in text:
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        return blocks

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
# 5а. Предупреждения/примечания -> blockquote-callout с ⚠️
#     (правило добавлено для читаемости: важные пометки не должны
#     теряться среди обычного текста)
# ---------------------------------------------------------------------------

_CALLOUT_MARKERS = [
    r"эслатма", r"муҳим(?:\s+эслатма)?", r"диққат", r"эътибор беринг",
    r"eslatma", r"muhim(?:\s+eslatma)?", r"diqqat", r"eʼtibor bering",
    r"внимание", r"важно", r"примечание", r"обратите внимание",
]
_CALLOUT_RE = re.compile(
    r"^\s*(?:" + "|".join(_CALLOUT_MARKERS) + r")\s*[:!.,—-]*\s*",
    flags=re.IGNORECASE,
)


def _maybe_callout(paragraph: str) -> Optional[str]:
    """Если абзац начинается со слова-маркера примечания/предупреждения,
    оформляем его как blockquote с иконкой — визуально выделяется на
    фоне остального текста."""
    m = _CALLOUT_RE.match(paragraph)
    if not m or not m.group(0).strip():
        return None
    rest = paragraph[m.end() :].strip()
    if not rest:
        return None
    return "> ⚠️ " + rest


# ---------------------------------------------------------------------------
# 5б. Определения и цитаты -> blockquote
# ---------------------------------------------------------------------------

_DEFINITION_MARKERS = [
    r"deb\s+tushuniladi", r"деб\s+тушунилади",
    r"deb\s+topiladi", r"деб\s+топилади",
    r"признаётся", r"признается",
    r"понимается\s+как", r"означает",
]
_DEFINITION_RE = re.compile(
    r"\b(?:" + "|".join(_DEFINITION_MARKERS) + r")\b", flags=re.IGNORECASE
)

# Прямая цитата в кавычках длиной больше одного предложения.
_QUOTED_RE = re.compile(r"[\"«]([^\"»]{20,})[\"»]")


def _maybe_blockquote(paragraph: str) -> Optional[str]:
    if _DEFINITION_RE.search(paragraph):
        return "> " + paragraph.strip()

    m = _QUOTED_RE.search(paragraph)
    if m and len(_SENTENCE_SPLIT_RE.split(m.group(1))) > 1:
        return "> " + paragraph.strip()

    return None


# ---------------------------------------------------------------------------
# 6. Нумерованные списки (с вложенными a)/b) подпунктами)
# ---------------------------------------------------------------------------

_NUMBERED_ITEM_RE = re.compile(r"(?:(?<=^)|(?<=\s))(\d{1,2})[.)]\s+")
_SUBLETTER_ITEM_RE = re.compile(
    r"(?:(?<=^)|(?<=\s))([a-zа-яё])\)\s+", flags=re.IGNORECASE
)


def _extract_numbered_items(paragraph: str) -> Optional[List[str]]:
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

    prefix = paragraph[: matches[0].start()].strip()
    if prefix:
        items.insert(0, "__PREFIX__" + prefix)

    return items if items else None


def _split_subletters(item_text: str) -> Optional[List[str]]:
    """Если внутри пункта нумерованного списка есть 'a) ... b) ...' —
    возвращает вложенные подпункты (без вводного текста перед 'a)')."""
    matches = list(_SUBLETTER_ITEM_RE.finditer(item_text))
    if len(matches) < 2:
        return None
    subs = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(item_text)
        sub = item_text[start:end].strip().rstrip(";,")
        if sub:
            subs.append(sub)
    return subs if len(subs) >= 2 else None


# ---------------------------------------------------------------------------
# 7. Маркированные списки: словесные маркеры и однородные перечисления
# ---------------------------------------------------------------------------

_ENUM_MARKERS = [
    r"во[-\s]первых", r"во[-\s]вторых", r"в[-\s]третьих",
    r"кроме\s+того", r"также",
    r"birinchidan", r"ikkinchidan", r"uchinchidan",
    r"shuningdek", r"bundan\s+tashqari", r"shu\s+bilan\s+birga",
    r"биринчидан", r"иккинчидан", r"учинчидан",
    r"шунингдек", r"бундан\s+ташқари", r"шу\s+билан\s+бирга",
    # союзы-альтернативы (2+ повтора в абзаце -> явное перечисление
    # вариантов, тоже становится маркированным списком для читаемости)
    r"либо", r"или", r"yoki", r"yoxud",
]
_ENUM_MARKER_RE = re.compile(
    r"\b(?:" + "|".join(_ENUM_MARKERS) + r")\b", flags=re.IGNORECASE
)

_COMMA_LIST_RE = re.compile(r"^[^,:;]{1,60}(?:,\s*[^,:;]{1,60}){2,}\.?$")

_COMMA_LIST_WITH_INTRO_RE = re.compile(
    r"^(?P<intro>[^:]{1,120}):\s*"
    r"(?P<items>[^,:;]{1,60}(?:,\s*[^,:;]{1,60}){2,}\.?)$"
)


def _split_enum_markers(paragraph: str) -> Optional[List[str]]:
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
# 8. Ссылки на статьи закона -> inline code
# ---------------------------------------------------------------------------

_ARTICLE_REF_RE = re.compile(
    r"(часть\s+\d+\s+статьи\s+\d+|статья\s+\d+|статьи\s+\d+"
    r"|\d+[-\s]?модда|\d+[-\s]?modda|закон\s*№\s*\d+|qonun\s*№\s*\d+)",
    flags=re.IGNORECASE,
)


def format_article_refs(text: str) -> str:
    """Оборачивает ссылки на статьи/законы в inline code."""

    def _wrap(m: re.Match) -> str:
        return f"`{m.group(0)}`"

    return _ARTICLE_REF_RE.sub(_wrap, text)


# ---------------------------------------------------------------------------
# 8а. Числа: даты, проценты, денежные суммы -> inline code
#     (правило для читаемости: конкретные цифры не должны теряться
#     в сплошном тексте, их проще заметить моноширинным шрифтом)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(
    r"(?<![`\w])"
    r"(?:"
    r"\d{1,2}[./]\d{1,2}[./]\d{2,4}"                       # 12.05.2024
    r"|\d{4}[-\s](?:йил|yil|году?|года)"                    # 2024-yil / 2024 йил
    r"|\d+(?:[.,]\d+)?\s?%"                                  # 15% / 15,5 %
    r"|\d[\d\s]{0,12}\d?\s?(?:so['ʻʼ]m|сўм|сум|у\.е\.|доллар|dollar)"
    r")"
    r"(?![\w`])",
    flags=re.IGNORECASE,
)


def format_numbers(text: str) -> str:
    """Оборачивает даты/проценты/суммы в inline code, чтобы они не
    сливались с обычным текстом при чтении."""

    def _wrap(m: re.Match) -> str:
        return f"`{m.group(0).strip()}`"

    return _NUMBER_RE.sub(_wrap, text)


# ---------------------------------------------------------------------------
# 9. Аббревиатуры -> расшифровка при первом упоминании
# ---------------------------------------------------------------------------


def expand_abbreviations(text: str, seen: set) -> str:
    def _wrap(m: re.Match) -> str:
        abbr = m.group(0)
        if abbr in seen:
            return abbr
        seen.add(abbr)
        full = ABBREVIATIONS.get(abbr)
        if not full:
            return abbr
        return f"**{abbr}** ({full})"

    pattern = r"\b(?:" + "|".join(re.escape(a) for a in ABBREVIATIONS) + r")\b"
    return re.sub(pattern, _wrap, text)


# ---------------------------------------------------------------------------
# 10. Таблицы: регулярная структура "X — Y" (3+ раза подряд)
# ---------------------------------------------------------------------------

_TABLE_ROW_RE = re.compile(r"^(.{1,60}?)\s*[—–-]\s*(.{1,60})$")


def _try_build_table(paragraphs: List[str]) -> Optional[List[str]]:
    """
    Если 3+ соседних абзаца выглядят как 'Нарушение — Санкция',
    склеивает их в одну markdown-таблицу и возвращает НОВЫЙ список
    абзацев (с таблицей вместо распознанных строк). Иначе — None.
    """
    rows = []
    idxs = []
    for i, p in enumerate(paragraphs):
        m = _TABLE_ROW_RE.match(p.strip())
        if m:
            rows.append((m.group(1).strip(), m.group(2).strip()))
            idxs.append(i)
        else:
            if len(rows) >= 3:
                break
            rows, idxs = [], []
    if len(rows) < 3:
        return None

    start, end = idxs[0], idxs[-1]
    table_lines = ["| Нарушение | Санкция |", "| --- | --- |"]
    for left, right in rows:
        table_lines.append(f"| {left} | {right} |")
    table_block = "\n".join(table_lines)

    return paragraphs[:start] + [table_block] + paragraphs[end + 1 :]


# ---------------------------------------------------------------------------
# 11. Сборка одного абзаца в Markdown-блок
# ---------------------------------------------------------------------------


def _render_list(items: List[str], numbered: bool) -> str:
    lines = []
    counter = 1
    seen_terms: set = set()
    seen_abbr: set = set()
    for item in items:
        if item.startswith("__PREFIX__"):
            raw = item[len("__PREFIX__") :]
            raw = format_article_refs(raw)
            raw = format_numbers(raw)
            raw = bold_legal_terms(raw, seen_terms)
            lines.append(expand_abbreviations(raw, seen_abbr))
            continue

        subs = _split_subletters(item) if numbered else None
        marker = f"{counter}. " if numbered else "- "
        if subs is not None:
            head = _SUBLETTER_ITEM_RE.split(item, maxsplit=1)[0].strip().rstrip(":")
            head = format_article_refs(head)
            head = format_numbers(head)
            head = bold_legal_terms(head, seen_terms)
            lines.append(marker + expand_abbreviations(head, seen_abbr))
            letters = "абвгдеж"
            for j, sub in enumerate(subs):
                sub = format_article_refs(sub)
                sub = format_numbers(sub)
                sub = bold_legal_terms(sub, seen_terms)
                sub = expand_abbreviations(sub, seen_abbr)
                indent = "    " if numbered else "  "
                letter = letters[j] if j < len(letters) else str(j + 1)
                lines.append(f"{indent}{letter}) " + sub)
        else:
            rendered = format_article_refs(item)
            rendered = format_numbers(rendered)
            rendered = bold_legal_terms(rendered, seen_terms)
            lines.append(marker + expand_abbreviations(rendered, seen_abbr))
        if numbered:
            counter += 1
    return "\n".join(lines)


def _format_paragraph(paragraph: str) -> str:
    paragraph = paragraph.strip()
    if not paragraph:
        return ""

    header = _format_section_header(paragraph)
    if header:
        return header

    article_header = _format_article_header(paragraph)
    if article_header:
        return article_header

    callout = _maybe_callout(paragraph)
    if callout:
        callout_body = callout[len("> ⚠️ ") :]
        callout_body = format_article_refs(callout_body)
        callout_body = format_numbers(callout_body)
        callout_body = bold_legal_terms(callout_body, set())
        callout_body = expand_abbreviations(callout_body, set())
        return "> ⚠️ " + callout_body

    quote = _maybe_blockquote(paragraph)
    if quote:
        quote_body = quote[2:]
        quote_body = format_article_refs(quote_body)
        quote_body = format_numbers(quote_body)
        quote_body = bold_legal_terms(quote_body, set())
        quote_body = expand_abbreviations(quote_body, set())
        return "> " + quote_body

    numbered_items = _extract_numbered_items(paragraph)
    if numbered_items:
        return _render_list(numbered_items, numbered=True)

    marker_items = _split_enum_markers(paragraph)
    if marker_items:
        return _render_list(marker_items, numbered=False)

    comma_items = _split_comma_enumeration(paragraph)
    if comma_items:
        return _render_list(comma_items, numbered=False)

    rendered = format_article_refs(paragraph)
    rendered = format_numbers(rendered)
    rendered = bold_legal_terms(rendered, set())
    return expand_abbreviations(rendered, set())


# ---------------------------------------------------------------------------
# 12. Публичная функция
# ---------------------------------------------------------------------------


def to_markdown(text: str) -> str:
    """
    Детерминированно преобразует упрощённый узбекский текст в
    структурированный Markdown: заголовки, абзацы, списки (в т.ч.
    вложенные), определения/цитаты, ссылки на статьи, таблицы и
    **выделение** частотных юридических терминов.

    Никаких вызовов модели/API — только regex и словарный поиск.
    Идемпотентна: повторный вызов на уже отформатированном выводе не
    меняет текст (см. _looks_like_markdown).
    """
    if text is None:
        return ""
    text = text.strip()
    if not text:
        return ""

    if _looks_like_markdown(text):
        return text

    title, body = extract_title(text)

    paragraphs = split_into_paragraphs(body)
    if not paragraphs:
        return ("# " + title) if title else ""

    table_paragraphs = _try_build_table(paragraphs)
    if table_paragraphs is not None:
        paragraphs = table_paragraphs

    blocks: List[str] = []
    for p in paragraphs:
        if p.startswith("| "):
            blocks.append(p)
            continue

        formatted = _format_paragraph(p)
        if not formatted:
            continue

        is_section_header = formatted.startswith("## ")
        if is_section_header and blocks:
            blocks.append("---")

        blocks.append(formatted)

    if title:
        blocks.insert(0, f"# {title}")

    return "\n\n".join(blocks)
