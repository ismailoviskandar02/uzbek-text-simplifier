"""
Парсер узбекских новостей + Википедии для сбора корпуса (text simplification project).

Собирает статьи из RSS-фидов и Wikipedia API, чистит служебные артефакты
разметки, разбивает текст на чанки по 100-200 слов (по границам предложений),
оценивает сложность каждого чанка и сохраняет всё в CSV:
source, title, url, chunk_id, text, word_count, length,
avg_sentence_len, avg_word_len, rare_word_ratio, complexity_score.

CSV пишется с QUOTE_ALL — каждое поле в кавычках, устойчиво к тексту с
большим числом запятых/кавычек внутри (стандартный pandas.read_csv читает
такой файл без проблем).

Использование:
    pip install feedparser requests beautifulsoup4 trafilatura pandas --break-system-packages
    python uz_news_parser.py
"""

import time
import csv
import re
from datetime import datetime
from collections import Counter

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Настройка источников
# ---------------------------------------------------------------------------
# ВАЖНО про CSS-селекторы: раньше тут был жёстко заданный класс на источник
# (типа "div.article-body"), но верстка сайтов меняется/отличается по разделам,
# из-за чего парсер либо ничего не находил, либо хватал мусор (меню, рекламу).
#
# Вместо этого используем trafilatura — библиотеку, которая сама определяет
# основной текст статьи на странице (так же, как это делает режим "Reader"
# в браузере), без привязки к конкретной верстке сайта. Это менее хрупко и
# не требует ручной подгонки селектора под каждый источник.

SOURCES = [
    {
        "name": "kun.uz",
        "rss": "https://kun.uz/uz/news/rss",
    },
    {
        "name": "uza.uz",
        # Официальное национальное агентство — проверенный рабочий RSS.
        # (daryo.uz убран: не нашёл подтверждённого публичного RSS-адреса —
        # если найдёшь его сам через Ctrl+U на сайте, просто добавь сюда.)
        "rss": "https://uza.uz/uz/rss",
    },
    {
        "name": "gazeta.uz",
        "rss": "https://www.gazeta.uz/uz/rss/",
    },
    # nuz.uz был добавлен как 4-й источник, но при первом прогоне выяснилось:
    # его RSS (https://nuz.uz/feed/) отдаёт РУССКОЯЗЫЧНЫЙ контент, несмотря на
    # домен .uz — сам фид, не ошибка извлечения текста. У сайта есть узбекская
    # версия (nuz.uz/uz/...), но её отдельный RSS-адрес найти не удалось —
    # убран, чтобы не засорять узбекский корпус русским текстом. Если найдёшь
    # рабочий узбекский RSS этого сайта вручную — можно добавить обратно.
]

# Wikipedia не имеет RSS, тянем через MediaWiki API отдельной функцией ниже.
WIKI_API_URL = "https://uz.wikipedia.org/w/api.php"
WIKI_CATEGORIES = [
    # Категории для разнообразия тем и стиля.
    # Было 4 категории (история, физика, экономика, медицина) — расширено
    # до 14 для роста объёма корпуса (цель: 800-1500+ чанков вместо 249)
    # и большего разнообразия стиля/лексики для parallel-корпуса.
    #
    # Написание со спецсимволами проверено вручную по реальным статьям —
    # в узбекском Latin два разных спецсимвола: ʻ (teskari tutuq, "o'zbek"
    # звук) и ʼ (tutuq belgisi, гортанная смычка в "ta'lim", "san'at").
    # Обычный ASCII-апостроф ' почти всегда не совпадает с реальным тайтлом
    # категории в MediaWiki (точное совпадение обязательно) — из-за этого
    # "Ta'lim" в первом прогоне не дал вообще ни одной статьи.
    "Oʻzbekiston tarixi",
    "Fizika",
    "Iqtisodiyot",
    "Tibbiyot",
    "Geografiya",
    "Kimyo",
    "Biologiya",
    "Huquq",
    "Taʼlim",
    "Adabiyot",
    "Sanʼat",
    "Siyosat",
    "Ekologiya",
    "Din",
]
MAX_WIKI_ARTICLES_PER_CATEGORY = 80  # было 40 — категорий стало больше, лимит на каждую тоже поднят
WIKI_BATCH_SIZE = 20  # сколько статей запрашивать за один вызов API (макс. 50)

# Wikimedia требует описательный User-Agent с контактом — без этого сервер
# агрессивнее режет запросы лимитами (это и была причина 429 Too Many Requests).
# Подставь свой реальный контакт (email/Telegram/GitHub) вместо примера ниже.
WIKI_HEADERS = {
    "User-Agent": (
        "UzTextSimplificationBot/1.0 "
        "(student capstone project; contact: your-email@example.com) "
        "python-requests"
    )
}

# А вот новостные сайты (gazeta.uz отдавал 403 Forbidden на статьях) реагируют
# ровно наоборот: у них обычно Cloudflare/анти-бот защита, которая блокирует
# запросы БЕЗ реалистичного браузерного User-Agent — им как раз нужен обычный
# Chrome UA + типичные для браузера заголовки, а не честное "я бот для учёбы".
NEWS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "uz,ru;q=0.9,en;q=0.8",
}

REQUEST_DELAY_SEC = 2.0  # пауза между запросами, чтобы не долбить сайт
WIKI_DELAY_SEC = 1.0     # отдельная пауза для Wikipedia (после батчинга запросов меньше)
MAX_ARTICLES_PER_SOURCE = 150
MIN_TEXT_LENGTH = 200  # отбрасываем слишком короткие/пустые статьи
MAX_RETRIES = 3        # повторные попытки при 429/сетевых ошибках

# ---------------------------------------------------------------------------
# Перевод (ru/en) — ТОЛЬКО для ручной проверки качества упрощения,
# не для всего корпуса. Переводить 500 статей на 2 языка через API — это
# лишние деньги/квота и время, которые не нужны на этапе сбора корпуса.
# Включай TRANSLATE_SAMPLE и указывай, сколько случайных статей перевести.
# ---------------------------------------------------------------------------
TRANSLATE_SAMPLE = False          # поставь True, когда захочешь прогнать выборку
TRANSLATE_SAMPLE_SIZE = 15        # сколько статей перевести для ручной проверки
GEMINI_API_KEY = ""               # вставь свой ключ, либо возьми из os.environ
GEMINI_MODEL = "gemini-2.5-flash"


def clean_text(raw_html_text) -> str:
    """Убирает лишние пробелы/переносы, оставляет чистый текст."""
    if not raw_html_text:
        return ""
    text = re.sub(r"\s+", " ", raw_html_text)
    return text.strip()


# ---------------------------------------------------------------------------
# Очистка артефактов разметки (в основном Wikipedia, но применяется ко всем
# источникам на всякий случай)
# ---------------------------------------------------------------------------
# explaintext=1 в Wikipedia API убирает большую часть wiki-разметки, но
# оставляет: служебные заголовки разделов (== Manbalar ==, == Havolalar ==),
# остатки шаблонов ({{...}}, Andoza:...), голые ссылки и упоминания архивов
# (Wayback Machine). Всё это — не часть содержательного текста статьи и
# должно быть вырезано до того, как текст пойдёт в датасет.

# Разделы, начиная с которых и до конца статьи всё — служебное (источники,
# ссылки, литература), а не содержательный текст. Если встречаем такой
# заголовок — обрезаем текст статьи на этом месте.
WIKI_TRAILING_SECTIONS = [
    "Manbalar", "Adabiyotlar", "Havolalar", "Tashqi havolalar",
    "Izohlar", "Eslatmalar", "Shuningdek qarang", "Adabiyot",
]
_trailing_pattern = "|".join(re.escape(s) for s in WIKI_TRAILING_SECTIONS)
WIKI_CUT_RE = re.compile(rf"==\s*(?:{_trailing_pattern})\s*==.*$", re.DOTALL)

# Остальной мусор — вырезаем точечно, а не обрезаем весь хвост текста
ARTIFACT_PATTERNS = [
    re.compile(r"\{\{[^{}]*\}\}"),                  # остатки шаблонов {{...}}
    re.compile(r"\bAndoza:\S*"),                     # "Andoza:..." (namespace шаблонов)
    re.compile(r"\bWayback Machine\b", re.IGNORECASE),
    re.compile(r"https?://\S+"),                     # голые URL
    re.compile(r"\bwww\.\S+"),                        # голые www-адреса без схемы
    re.compile(r"==+\s*[^=]{1,120}?\s*==+"),          # оставшиеся заголовки разделов "== ... =="
]


def strip_wiki_artifacts(text: str) -> str:
    """Убирает служебные секции/шаблоны/ссылки, оставляет только содержательный текст."""
    text = WIKI_CUT_RE.sub("", text)
    for pattern in ARTIFACT_PATTERNS:
        text = pattern.sub(" ", text)
    return clean_text(text)


# ---------------------------------------------------------------------------
# Разбивка на чанки по 100-200 слов (без разрыва предложений посередине)
# ---------------------------------------------------------------------------
# Целые статьи разного размера — плохая единица для parallel-корпуса
# (сложно/просто пары обычно делают на уровне абзаца-двух, не всей статьи).
# Разбиваем по границам предложений и группируем в чанки заданного размера.

CHUNK_MIN_WORDS = 100
CHUNK_MAX_WORDS = 200

# Узбекские новости часто заканчиваются короткой авторской подписью
# ("ЎзА.", "Т.Рўзиев, ЎзА", "Илова: ...") — при чанкинге по предложениям
# это превращается в отдельный "хвостовой" чанк из 1-3 слов, который не несёт
# содержательного текста и только засоряет датасет. Если последний остаток
# короче этого порога — не создаём под него отдельный чанк, а приклеиваем
# к предыдущему (если он есть) или отбрасываем совсем (если чанков не было).
MIN_TAIL_CHUNK_WORDS = 15


def split_sentences_for_chunking(text: str) -> list[str]:
    """
    Разбивка на предложения с сохранением знака препинания (в отличие от
    split_sentences ниже, который используется только для метрик и не
    сохраняет пунктуацию — здесь она нужна, чтобы текст чанка остался читаемым).
    """
    # Разбиваем по .!? с учётом заглавной буквы дальше, чтобы не резать
    # сокращения/десятичные числа как отдельные предложения
    raw_sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZʻʼЎЁ\u0400-\u042F])", text)
    return [s.strip() for s in raw_sentences if s.strip()]


def chunk_text(text: str, min_words: int = CHUNK_MIN_WORDS,
               max_words: int = CHUNK_MAX_WORDS) -> list[str]:
    """
    Группирует предложения в чанки по 100-200 слов. Последний чанк может
    быть короче min_words, если в статье не хватило предложений — это
    нормально, короткие "хвостовые" чанки просто нужно потом отфильтровать
    по length при желании.
    """
    sentences = split_sentences_for_chunking(text)
    if not sentences:
        return []

    chunks = []
    current_sentences = []
    current_word_count = 0

    for sentence in sentences:
        sentence_word_count = len(sentence.split())
        current_sentences.append(sentence)
        current_word_count += sentence_word_count

        if current_word_count >= min_words:
            # Если уже перевалили за max_words — всё равно закрываем чанк
            # здесь (не дробим предложение), чуть больше 200 слов — это ок,
            # предложение не режем посередине.
            chunks.append(" ".join(current_sentences))
            current_sentences = []
            current_word_count = 0

    # Остаток короче min_words — это либо честный короткий хвост статьи,
    # либо (чаще) мусорная подпись автора в конце ("ЎзА.", "Т.Рўзиев, ЎзА").
    # Если он совсем короткий (< MIN_TAIL_CHUNK_WORDS) — не создаём под него
    # отдельный чанк: приклеиваем к предыдущему, если он есть, иначе просто
    # отбрасываем (лучше потерять пару слов подписи, чем засорить датасет
    # чанками из 1-3 слов).
    if current_sentences:
        tail_word_count = sum(len(s.split()) for s in current_sentences)
        tail_text = " ".join(current_sentences)
        if tail_word_count < MIN_TAIL_CHUNK_WORDS:
            if chunks:
                chunks[-1] = chunks[-1] + " " + tail_text
            # если chunks пуст — короткая статья целиком мусорная, просто пропускаем
        else:
            chunks.append(tail_text)

    return chunks


def fetch_with_retry(url: str, params: dict = None, timeout: int = 10, headers: dict = None):
    """GET-запрос с повторными попытками при 429/403/сетевых ошибках (exponential backoff)."""
    request_headers = headers if headers is not None else NEWS_HEADERS
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=request_headers, timeout=timeout)
            if resp.status_code in (429, 403):
                wait = 5 * attempt  # 5s, 10s, 15s...
                print(f"     [{resp.status_code}] жду {wait}с (попытка {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                print(f"  [!] Ошибка после {MAX_RETRIES} попыток: {e}")
                return None
            time.sleep(2 * attempt)
    return None


def fetch_article_text(url: str) -> str:
    """
    Скачивает страницу статьи и вытаскивает основной текст автоматически
    через trafilatura (без привязки к конкретной верстке сайта).
    """
    resp = fetch_with_retry(url)
    if resp is None:
        return ""

    extracted = trafilatura.extract(
        resp.text,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not extracted:
        return ""

    return clean_text(extracted)


def chunk_list(items: list, size: int) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


def fetch_category_member_titles(category: str, limit: int, max_subcat_depth: int = 1) -> list[str]:
    """
    Собирает названия статей категории, включая статьи из подкатегорий
    (на глубину max_subcat_depth).

    В узбекской Википедии у большинства тематических категорий ("Fizika",
    "Tibbiyot" и т.п.) почти нет статей напрямую — основная масса сидит в
    подкатегориях ("Fizika > Kvant fizikasi", "Fizika > Mexanika" и т.д.).
    Первый прогон это и показал: лимит 80 на категорию, а реально собралось
    1-4 статьи — потому что смотрели только прямых участников. Обход
    подкатегорий на 1 уровень вглубь решает проблему без риска зациклиться
    (категории могут ссылаться друг на друга) или уйти в бесконечную глубину.
    """
    titles: list[str] = []
    subcats_to_expand: list[str] = []

    list_params = {
        "action": "query",
        "list": "categorymembers",
        # ВАЖНО: пространство имён категорий в узбекской Википедии называется
        # "Turkum:", а НЕ "Kategoriya:" (которое использовалось раньше).
        # Из-за этого первый прогон с 14 категориями собрал всего 1-4 статьи
        # на категорию вместо ожидаемых 80 — запрос уходил в почти пустое
        # пространство имён. Проверено вручную: реальные статьи по физике
        # помечены "Turkum: Fizika", а не "Kategoriya:Fizika".
        "cmtitle": f"Turkum:{category}",
        "cmlimit": min(limit, 500),
        "format": "json",
    }
    resp = fetch_with_retry(WIKI_API_URL, params=list_params, headers=WIKI_HEADERS)
    if resp is None:
        return titles

    try:
        members = resp.json().get("query", {}).get("categorymembers", [])
    except ValueError as e:
        print(f"  [!] Ошибка разбора ответа для категории {category}: {e}")
        return titles

    for m in members:
        title = m["title"]
        if title.startswith("Turkum:"):
            subcats_to_expand.append(title.removeprefix("Turkum:"))
        else:
            titles.append(title)

    if len(titles) < limit and max_subcat_depth > 0:
        time.sleep(WIKI_DELAY_SEC)
        for subcat in subcats_to_expand:
            if len(titles) >= limit:
                break
            remaining = limit - len(titles)
            sub_titles = fetch_category_member_titles(
                subcat, remaining, max_subcat_depth=max_subcat_depth - 1
            )
            # dedupe на случай, если статья попадает в несколько подкатегорий
            for t in sub_titles:
                if t not in titles:
                    titles.append(t)

    return titles[:limit]


def fetch_wiki_category_articles(category: str, limit: int) -> list[dict]:
    """
    Получает список статей категории (включая 1 уровень подкатегорий) и их
    полный текст через MediaWiki API.

    Раньше запрос шёл по одной статье за раз (~40 запросов подряд без
    достаточной паузы) — Wikipedia отвечала 429 Too Many Requests уже
    на третьей-четвёртой статье. MediaWiki API поддерживает до 50 тайтлов
    в одном запросе (titles через "|") — это и решает проблему, а не просто
    увеличение паузы между запросами.
    """
    print(f"\n== Wikipedia: {category} ==")

    titles = fetch_category_member_titles(category, limit)
    if not titles:
        return []

    articles = []
    for batch in chunk_list(titles, WIKI_BATCH_SIZE):
        extract_params = {
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "titles": "|".join(batch),
            "format": "json",
        }
        resp = fetch_with_retry(WIKI_API_URL, params=extract_params, headers=WIKI_HEADERS)
        time.sleep(WIKI_DELAY_SEC)

        if resp is None:
            continue

        try:
            pages = resp.json().get("query", {}).get("pages", {})
        except ValueError:
            continue

        for page in pages.values():
            title = page.get("title", "")
            text = clean_text(page.get("extract", ""))
            text = strip_wiki_artifacts(text)

            if len(text) < MIN_TEXT_LENGTH:
                continue

            articles.append(
                {
                    "source": "wikipedia",
                    "title": title,
                    "url": f"https://uz.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    "published": "",
                    "text": text,
                    "length": len(text),
                }
            )
            print(f"  -> {title[:60]}")

    print(f"  Собрано статей: {len(articles)}")
    return articles


# ---------------------------------------------------------------------------
# Оценка сложности текста (readability)
# ---------------------------------------------------------------------------
# Простая эвристика без готовых NLP-библиотек под узбекский:
#   1. Средняя длина предложения (слов) — длиннее = сложнее
#   2. Доля "редких" слов — редкость считается относительно частотного
#      словаря, построенного по всему собранному корпусу (а не по одному тексту)
#   3. Средняя длина слова (символов) — длиннее слова часто = сложнее
#      морфологически (актуально для агглютинативного узбекского)
#
# Итоговый complexity_score — нормированная сумма трёх факторов (0 = просто, 1 = сложно).
# Это НЕ научная метрика, а рабочий baseline для сортировки/фильтрации корпуса
# на старте проекта. Позже можно заменить на что-то более строгое.

WORD_RE = re.compile(r"[a-zA-Zʻʼ'\u0400-\u04FF]+")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


def tokenize_words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def split_sentences(text: str) -> list[str]:
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]
    return sentences


def build_frequency_dict(all_texts: list[str]) -> Counter:
    """Строит частотный словарь по всему корпусу — нужен для rare_word_ratio."""
    counter = Counter()
    for text in all_texts:
        counter.update(tokenize_words(text))
    return counter


def compute_complexity(text: str, freq_dict: Counter, rare_threshold: int = 3) -> dict:
    """Считает метрики сложности текста относительно корпуса."""
    words = tokenize_words(text)
    sentences = split_sentences(text)

    if not words or not sentences:
        return {"avg_sentence_len": 0.0, "rare_word_ratio": 0.0,
                "avg_word_len": 0.0, "complexity_score": 0.0}

    avg_sentence_len = len(words) / len(sentences)
    avg_word_len = sum(len(w) for w in words) / len(words)

    rare_count = sum(1 for w in words if freq_dict.get(w, 0) <= rare_threshold)
    rare_word_ratio = rare_count / len(words)

    # Нормализация (подобраны эмпирически, подстрой под свой корпус после первого прогона)
    norm_sentence_len = min(avg_sentence_len / 25, 1.0)   # 25+ слов в предложении = максимум сложности
    norm_word_len = min(avg_word_len / 10, 1.0)           # 10+ символов в среднем слове = максимум
    norm_rare_ratio = min(rare_word_ratio, 1.0)

    complexity_score = round(
        0.4 * norm_sentence_len + 0.3 * norm_rare_ratio + 0.3 * norm_word_len, 3
    )

    return {
        "avg_sentence_len": round(avg_sentence_len, 2),
        "rare_word_ratio": round(rare_word_ratio, 3),
        "avg_word_len": round(avg_word_len, 2),
        "complexity_score": complexity_score,
    }


def translate_text(text: str, target_lang: str) -> str:
    """
    Переводит текст через Gemini API. Используется только для ручной
    проверки качества упрощения на небольшой выборке (не для всего корпуса).

    target_lang: "ru" или "en"
    """
    if not GEMINI_API_KEY:
        return "[нет GEMINI_API_KEY — перевод пропущен]"

    lang_name = {"ru": "русский", "en": "английский"}[target_lang]
    prompt = (
        f"Переведи следующий узбекский текст на {lang_name}. "
        f"Выведи только перевод, без комментариев и пояснений:\n\n{text}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(
            url,
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (requests.RequestException, KeyError, IndexError) as e:
        print(f"    [!] Ошибка перевода ({target_lang}): {e}")
        return ""


def translate_sample_for_qa(articles: list[dict], sample_size: int) -> None:
    """
    Берёт случайную выборку статей и добавляет им поля text_ru/text_en —
    чисто для того, чтобы ты вручную сверил смысл при проверке упрощения.
    Модифицирует articles на месте (остальные статьи поля не получают).
    """
    import random

    if not articles:
        return

    sample = random.sample(articles, min(sample_size, len(articles)))
    print(f"\nПеревожу выборку из {len(sample)} статей для ручной проверки...")

    for i, article in enumerate(sample, 1):
        print(f"  [{i}/{len(sample)}] {article['title'][:50]}")
        article["text_ru"] = translate_text(article["text"], "ru")
        time.sleep(1)
        article["text_en"] = translate_text(article["text"], "en")
        time.sleep(1)


def parse_source(source: dict) -> list[dict]:
    """Парсит один источник: RSS -> список статей с полным текстом."""
    print(f"\n== {source['name']} ==")
    feed = feedparser.parse(source["rss"])

    if not feed.entries:
        print(f"  [!] RSS пустой или недоступен: {source['rss']}")
        return []

    articles = []
    for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
        title = (getattr(entry, "title", "") or "").strip()
        url = (getattr(entry, "link", "") or "").strip()
        published = getattr(entry, "published", "") or getattr(entry, "updated", "")

        if not url:
            continue

        print(f"  -> {title[:60]}")
        text = fetch_article_text(url)
        time.sleep(REQUEST_DELAY_SEC)

        if len(text) < MIN_TEXT_LENGTH:
            print("     [пропущено: слишком короткий/пустой текст]")
            continue

        articles.append(
            {
                "source": source["name"],
                "title": title,
                "url": url,
                "published": published,
                "text": text,
                "length": len(text),
            }
        )

    print(f"  Собрано статей: {len(articles)}")
    return articles


def save_to_csv(rows: list[dict], fieldnames: list[str], filename: str) -> None:
    """
    Сохраняет CSV с настройками, устойчивыми к тексту с запятыми/кавычками:
      - QUOTE_ALL: КАЖДОЕ поле в кавычках, а не только те, где есть запятая —
        так парсер (в т.ч. pandas) однозначно видит границы полей, даже если
        в тексте много запятых и вложенных кавычек.
      - doublequote=True (по умолчанию): кавычка внутри текста экранируется
        удвоением ("" вместо "), это стандарт CSV (RFC 4180) — так и должно
        быть, это не баг, просто multiline-текст в кавычках может выглядеть
        непривычно, если открывать файл в блокноте, а не через csv-parser.
      - lineterminator="\\n": фиксируем перевод строки явно, чтобы не
        плодить лишние \\r\\n при повторном сохранении на Windows.
      - encoding="utf-8-sig": BOM в начале файла — без него Excel на Windows
        иногда неправильно определяет кодировку кириллицы/латиницы с диакритикой.
    """
    if not rows:
        print("Нет данных для сохранения.")
        return

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Сохранено {len(rows)} строк в {filename}")

    # Самопроверка: сразу читаем файл обратно тем же способом, каким его
    # будет читать pandas/ноутбук — если он не откроется или число строк не
    # совпадёт, узнаём об этом сразу, а не через час обработки в Colab.
    try:
        with open(filename, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            read_rows = list(reader)
        if len(read_rows) != len(rows):
            print(f"  [!] ВНИМАНИЕ: записано {len(rows)} строк, но при чтении обратно "
                  f"получилось {len(read_rows)} — файл может быть повреждён.")
        else:
            print(f"  Проверка чтения: OK, {len(read_rows)} строк читаются корректно.")
    except Exception as e:
        print(f"  [!] Ошибка самопроверки чтения CSV: {e}")


def build_chunk_records(articles: list[dict]) -> list[dict]:
    """
    Превращает список статей (source, title, url, text, ...) в плоский список
    чанков по 100-200 слов — так каждая строка CSV становится независимым
    куском текста подходящего размера для simplification-пар, а не целой
    статьёй произвольной длины.
    """
    chunk_records = []
    for article in articles:
        chunks = chunk_text(article["text"])
        for i, chunk in enumerate(chunks, 1):
            chunk_records.append(
                {
                    "source": article["source"],
                    "title": article["title"],
                    "url": article["url"],
                    "chunk_id": i,
                    "text": chunk,
                    "word_count": len(chunk.split()),
                    "length": len(chunk),
                }
            )
    return chunk_records


def main():
    all_articles = []

    # 1. Новости через RSS
    for source in SOURCES:
        all_articles.extend(parse_source(source))

    # 2. Wikipedia через MediaWiki API (обычно самый разнообразный по стилю источник)
    for category in WIKI_CATEGORIES:
        all_articles.extend(
            fetch_wiki_category_articles(category, MAX_WIKI_ARTICLES_PER_CATEGORY)
        )

    if not all_articles:
        print("Ничего не собрано — проверь RSS/API доступность.")
        return

    # 3. Разбиваем каждую статью на чанки по 100-200 слов
    print("\nРазбиваю статьи на чанки по 100-200 слов...")
    chunk_records = build_chunk_records(all_articles)
    print(f"Получено {len(chunk_records)} чанков из {len(all_articles)} статей.")

    # 4. Частотный словарь строим по ВСЕМ чанкам —
    #    так "редкость" слова оценивается относительно реального распределения
    #    в собранном корпусе, а не одного текста.
    print("Строю частотный словарь по корпусу...")
    freq_dict = build_frequency_dict([c["text"] for c in chunk_records])

    # 5. Считаем сложность для каждого чанка
    for chunk in chunk_records:
        metrics = compute_complexity(chunk["text"], freq_dict)
        chunk.update(metrics)

    # 6. Сортируем по возрастанию сложности
    chunk_records.sort(key=lambda c: c["complexity_score"])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    fieldnames = [
        "source", "title", "url", "chunk_id", "text", "word_count", "length",
        "avg_sentence_len", "avg_word_len", "rare_word_ratio", "complexity_score",
    ]
    save_to_csv(chunk_records, fieldnames, filename=f"uz_corpus_{timestamp}.csv")

    scores = [c["complexity_score"] for c in chunk_records]
    print(f"\nДиапазон сложности: {min(scores):.3f} — {max(scores):.3f}")
    print(f"Медиана: {sorted(scores)[len(scores)//2]:.3f}")

    # 7. Опционально: перевод небольшой выборки на ru/en для ручной QA-проверки.
    #    НЕ применяется ко всему корпусу — только к TRANSLATE_SAMPLE_SIZE чанкам.
    if TRANSLATE_SAMPLE:
        sample_copy = [dict(c) for c in chunk_records]
        translate_sample_for_qa(sample_copy, TRANSLATE_SAMPLE_SIZE)
        qa_records = [c for c in sample_copy if "text_ru" in c]

        qa_fieldnames = [
            "source", "title", "url", "chunk_id", "text", "text_ru", "text_en",
            "complexity_score",
        ]
        save_to_csv(qa_records, qa_fieldnames, filename=f"uz_corpus_qa_sample_{timestamp}.csv")


if __name__ == "__main__":
    main()
