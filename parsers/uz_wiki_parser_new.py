"""
Парсер НОВЫХ статей Wikipedia для узбекского корпуса (text simplification project).

В отличие от uz_news_parser.py/2/3.py (которые тянут RSS-новости + Wikipedia
и каждый раз собирают всё заново, что даёт кучу пересечений между прогонами —
как было с uz_corpus_20260723_1106/1111/1117.csv), этот скрипт:

  1. Работает ТОЛЬКО с Wikipedia (MediaWiki API), новости не трогает.
  2. Перед сохранением статьи проверяет её URL по уже собранному корпусу
     (EXISTING_CORPUS_FILES) и пропускает то, что уже есть — на выходе
     только новые статьи, дублировать существующий корпус не нужно.
  3. Сразу проставляет колонку `domain` по категории Wikipedia, из которой
     взята статья (CATEGORY_TO_DOMAIN) — не нужно докласифицировать
     эвристикой постфактум, как пришлось делать для файлов без domain.
  4. В список категорий добавлены "Sport" и "Informatika" — по итогам
     прошлого отчёта в корпусе не хватало domain=sport и domain=technology.

Использование:
    pip install requests pandas --break-system-packages
    python uz_wiki_parser_new.py

Результат: uz_corpus_wiki_new_<timestamp>.csv с теми же колонками, что и
у остальных файлов корпуса (+ domain), готов к склейке через тот же пайплайн
дедупа, что уже использовался для merged-файла.
"""

import time
import csv
import os
import re
from datetime import datetime
from collections import Counter

import requests
import pandas as pd

# ---------------------------------------------------------------------------
# Что уже собрано — чтобы не тащить повторно
# ---------------------------------------------------------------------------
# Укажи здесь все CSV, которые уже входят в твой корпус (в т.ч. итоговый
# склеенный файл). Скрипт прочитает из них колонку `url` и не будет второй
# раз запрашивать/сохранять те же статьи.
EXISTING_CORPUS_FILES = [
    "uz_corpus_merged_final.csv",
    # добавляй сюда новые файлы по мере накопления, например:
    # "uz_corpus_wiki_new_20260724_0900.csv",
]


def load_existing_urls(files: list[str]) -> set[str]:
    urls = set()
    for f in files:
        if not os.path.exists(f):
            print(f"  [!] Файл не найден, пропускаю: {f}")
            continue
        try:
            df = pd.read_csv(f, usecols=["url"])
            urls.update(df["url"].dropna().astype(str).tolist())
        except Exception as e:
            print(f"  [!] Не смог прочитать {f}: {e}")
    print(f"Уже в корпусе (уникальных URL): {len(urls)}")
    return urls


# ---------------------------------------------------------------------------
# Wikipedia API
# ---------------------------------------------------------------------------
WIKI_API_URL = "https://uz.wikipedia.org/w/api.php"

# Категория -> domain. Домены совпадают со значениями, которые уже
# использовались в корпусе (law/science/economy/culture/sport/politics/
# education/health/technology/other), чтобы новый файл сразу лёг в общую
# разметку без ручной докласификации.
WIKI_CATEGORIES = {
    "Oʻzbekiston tarixi": "culture",
    "Fizika": "science",
    "Iqtisodiyot": "economy",
    "Tibbiyot": "health",
    "Geografiya": "science",
    "Kimyo": "science",
    "Biologiya": "science",
    "Huquq": "law",
    "Taʼlim": "education",
    "Adabiyot": "culture",
    "Sanʼat": "culture",
    "Siyosat": "politics",
    "Ekologiya": "science",
    "Din": "other",
    # Новые категории — закрывают домены, которых не хватало в корпусе
    # (проверено вручную, что категория существует и не пустая):
    "Sport": "sport",
    "Informatika": "technology",
}

MAX_WIKI_ARTICLES_PER_CATEGORY = 150
WIKI_BATCH_SIZE = 20          # макс. 50 тайтлов за один запрос extracts
MAX_SUBCAT_DEPTH = 2          # см. комментарий в fetch_category_member_titles
WIKI_DELAY_SEC = 1.0
MAX_RETRIES = 3
MIN_TEXT_LENGTH = 200

WIKI_HEADERS = {
    "User-Agent": (
        "UzTextSimplificationBot/1.0 "
        "(student capstone project; contact: your-email@example.com) "
        "python-requests"
    )
}

CHUNK_MIN_WORDS = 100
CHUNK_MAX_WORDS = 200
MIN_TAIL_CHUNK_WORDS = 15


# ---------------------------------------------------------------------------
# Очистка текста (та же логика, что и в uz_news_parser3.py)
# ---------------------------------------------------------------------------
def clean_text(raw_text) -> str:
    if not raw_text:
        return ""
    return re.sub(r"\s+", " ", raw_text).strip()


WIKI_TRAILING_SECTIONS = [
    "Manbalar", "Adabiyotlar", "Havolalar", "Tashqi havolalar",
    "Izohlar", "Eslatmalar", "Shuningdek qarang", "Adabiyot",
]
_trailing_pattern = "|".join(re.escape(s) for s in WIKI_TRAILING_SECTIONS)
WIKI_CUT_RE = re.compile(rf"==\s*(?:{_trailing_pattern})\s*==.*$", re.DOTALL)

ARTIFACT_PATTERNS = [
    re.compile(r"\{\{[^{}]*\}\}"),
    re.compile(r"\bAndoza:\S*"),
    re.compile(r"\bWayback Machine\b", re.IGNORECASE),
    re.compile(r"https?://\S+"),
    re.compile(r"\bwww\.\S+"),
    re.compile(r"==+\s*[^=]{1,120}?\s*==+"),
]


def strip_wiki_artifacts(text: str) -> str:
    text = WIKI_CUT_RE.sub("", text)
    for pattern in ARTIFACT_PATTERNS:
        text = pattern.sub(" ", text)
    return clean_text(text)


# ---------------------------------------------------------------------------
# Чанкинг по 100-200 слов (без разрыва предложений)
# ---------------------------------------------------------------------------
def split_sentences_for_chunking(text: str) -> list[str]:
    raw_sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZʻʼЎЁ\u0400-\u042F])", text)
    return [s.strip() for s in raw_sentences if s.strip()]


def chunk_text(text: str, min_words: int = CHUNK_MIN_WORDS,
               max_words: int = CHUNK_MAX_WORDS) -> list[str]:
    sentences = split_sentences_for_chunking(text)
    if not sentences:
        return []

    chunks = []
    current_sentences = []
    current_word_count = 0

    for sentence in sentences:
        current_sentences.append(sentence)
        current_word_count += len(sentence.split())

        if current_word_count >= min_words:
            chunks.append(" ".join(current_sentences))
            current_sentences = []
            current_word_count = 0

    if current_sentences:
        tail_word_count = sum(len(s.split()) for s in current_sentences)
        tail_text = " ".join(current_sentences)
        if tail_word_count < MIN_TAIL_CHUNK_WORDS:
            if chunks:
                chunks[-1] = chunks[-1] + " " + tail_text
        else:
            chunks.append(tail_text)

    return chunks


# ---------------------------------------------------------------------------
# HTTP с повторными попытками
# ---------------------------------------------------------------------------
def fetch_with_retry(url: str, params: dict = None, timeout: int = 10):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=WIKI_HEADERS, timeout=timeout)
            if resp.status_code in (429, 403):
                wait = 5 * attempt
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


def chunk_list(items: list, size: int) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


# ---------------------------------------------------------------------------
# Список статей категории (с обходом подкатегорий)
# ---------------------------------------------------------------------------
def fetch_category_member_titles(category: str, limit: int, max_subcat_depth: int = MAX_SUBCAT_DEPTH) -> list[str]:
    """
    В узбекской Wikipedia пространство имён категорий называется "Turkum:"
    (не "Kategoriya:"). Большинство содержательных статей лежит в
    подкатегориях 1-2 уровня вглубь, поэтому обходим рекурсивно.
    """
    titles: list[str] = []
    subcats_to_expand: list[str] = []

    list_params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Turkum:{category}",
        "cmlimit": min(limit, 500),
        "format": "json",
    }
    resp = fetch_with_retry(WIKI_API_URL, params=list_params)
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
            for t in sub_titles:
                if t not in titles:
                    titles.append(t)

    return titles[:limit]


def fetch_wiki_category_articles(category: str, domain: str, limit: int,
                                  existing_urls: set[str], seen_titles: set[str]) -> list[dict]:
    """
    Тянет полный текст статей категории через MediaWiki API батчами по
    WIKI_BATCH_SIZE тайтлов, ПРОПУСКАЯ статьи, чей url уже есть в
    existing_urls (уже собраны раньше) или title уже встретился в этом же
    прогоне (статья попала в несколько категорий/подкатегорий).
    """
    print(f"\n== Wikipedia: {category} -> domain={domain} ==")

    titles = fetch_category_member_titles(category, limit)
    # заранее убираем то, что точно уже есть в корпусе, чтобы не тратить
    # запросы extracts на статьи, которые всё равно выбросим
    new_titles = [
        t for t in titles
        if t not in seen_titles
        and f"https://uz.wikipedia.org/wiki/{t.replace(' ', '_')}" not in existing_urls
    ]
    skipped = len(titles) - len(new_titles)
    print(f"  Найдено тайтлов: {len(titles)}, уже в корпусе/дублей: {skipped}, новых к загрузке: {len(new_titles)}")

    if not new_titles:
        return []

    articles = []
    for batch in chunk_list(new_titles, WIKI_BATCH_SIZE):
        extract_params = {
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "titles": "|".join(batch),
            "format": "json",
        }
        resp = fetch_with_retry(WIKI_API_URL, params=extract_params)
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

            url = f"https://uz.wikipedia.org/wiki/{title.replace(' ', '_')}"
            seen_titles.add(title)

            articles.append(
                {
                    "source": "wikipedia",
                    "title": title,
                    "url": url,
                    "text": text,
                    "length": len(text),
                    "domain": domain,
                }
            )
            print(f"  -> {title[:60]}")

    print(f"  Собрано новых статей: {len(articles)}")
    return articles


# ---------------------------------------------------------------------------
# Оценка сложности (та же эвристика, что в остальных парсерах —
# нужна согласованность метрик по всему корпусу)
# ---------------------------------------------------------------------------
WORD_RE = re.compile(r"[a-zA-Zʻʼ'\u0400-\u04FF]+")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


def tokenize_words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]


def build_frequency_dict(all_texts: list[str]) -> Counter:
    freq = Counter()
    for text in all_texts:
        freq.update(tokenize_words(text))
    return freq


def compute_complexity(text: str, freq_dict: Counter, rare_threshold: int = 3) -> dict:
    words = tokenize_words(text)
    sentences = split_sentences(text)
    if not words or not sentences:
        return {
            "avg_sentence_len": 0.0, "avg_word_len": 0.0,
            "rare_word_ratio": 0.0, "complexity_score": 0.0,
        }

    avg_sentence_len = len(words) / len(sentences)
    avg_word_len = sum(len(w) for w in words) / len(words)
    rare_words = sum(1 for w in words if freq_dict.get(w, 0) <= rare_threshold)
    rare_word_ratio = rare_words / len(words)

    # та же нормировка, что и в uz_news_parser3.py, чтобы шкала совпадала
    norm_sentence_len = min(avg_sentence_len / 30, 1.0)
    norm_word_len = min(avg_word_len / 12, 1.0)
    complexity_score = round(
        0.4 * norm_sentence_len + 0.3 * norm_word_len + 0.3 * rare_word_ratio, 3
    )

    return {
        "avg_sentence_len": round(avg_sentence_len, 2),
        "avg_word_len": round(avg_word_len, 2),
        "rare_word_ratio": round(rare_word_ratio, 3),
        "complexity_score": complexity_score,
    }


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def save_to_csv(rows: list[dict], fieldnames: list[str], filename: str) -> None:
    if not rows:
        print("Нет новых данных для сохранения.")
        return

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL,
            lineterminator="\n", extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Сохранено {len(rows)} строк в {filename}")

    try:
        with open(filename, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            read_rows = list(reader)
        if len(read_rows) != len(rows):
            print(f"  [!] ВНИМАНИЕ: записано {len(rows)}, прочитано обратно {len(read_rows)}")
        else:
            print(f"  Проверка чтения: OK, {len(read_rows)} строк.")
    except Exception as e:
        print(f"  [!] Ошибка самопроверки чтения CSV: {e}")


def build_chunk_records(articles: list[dict]) -> list[dict]:
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
                    "domain": article["domain"],
                }
            )
    return chunk_records


def main():
    print("Загружаю список уже собранных URL...")
    existing_urls = load_existing_urls(EXISTING_CORPUS_FILES)

    all_articles = []
    seen_titles: set[str] = set()

    for category, domain in WIKI_CATEGORIES.items():
        all_articles.extend(
            fetch_wiki_category_articles(
                category, domain, MAX_WIKI_ARTICLES_PER_CATEGORY,
                existing_urls, seen_titles,
            )
        )

    if not all_articles:
        print("\nНовых статей не найдено (либо API недоступен, либо все категории уже выбраны).")
        return

    print(f"\nВсего новых статей: {len(all_articles)}")
    print("Разбиваю на чанки по 100-200 слов...")
    chunk_records = build_chunk_records(all_articles)
    print(f"Получено {len(chunk_records)} чанков.")

    print("Строю частотный словарь по новому набору...")
    freq_dict = build_frequency_dict([c["text"] for c in chunk_records])

    for chunk in chunk_records:
        metrics = compute_complexity(chunk["text"], freq_dict)
        chunk.update(metrics)

    chunk_records.sort(key=lambda c: c["complexity_score"])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    fieldnames = [
        "source", "title", "url", "chunk_id", "text", "word_count", "length",
        "avg_sentence_len", "avg_word_len", "rare_word_ratio", "complexity_score",
        "domain",
    ]
    filename = f"uz_corpus_wiki_new_{timestamp}.csv"
    save_to_csv(chunk_records, fieldnames, filename)

    print("\nПо доменам:")
    domain_counts = Counter(c["domain"] for c in chunk_records)
    for dom, cnt in domain_counts.most_common():
        print(f"  {dom}: {cnt}")


if __name__ == "__main__":
    main()
