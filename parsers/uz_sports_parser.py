"""
Парсер НОВОГО источника — sports.uz (без RSS, обход по листингу категорий).

Почему так: у sports.uz нет публичного RSS (проверено вручную), зато есть
обычная пагинация листинга новостей вида
    https://sports.uz/oz/news/<category>?page=N
(per-page у них зашит на 10 и не переопределяется параметром — не пытайся
задирать per-page, просто листай page=1,2,3...).

Категории (подтверждены по факту, реальные ссылки из меню сайта на
23.07.2026) — все относятся к домену `sport`, но подкатегория позволяет
при желании точнее фильтровать/анализировать конкретный вид спорта:

    football      - футбол
    uzbek_sports  - узбекский спорт (сборные, локальные события)
    boxs          - бокс
    tennis        - теннис
    solitary      - единоборства
    athletics     - лёгкая атлетика
    weightlifting - тяжёлая атлетика
    water-sports  - водные виды спорта
    chess         - шахматы
    winter-sports - зимние виды спорта
    automobile    - авто/мото
    olympism      - олимпийское движение
    others        - разное

Использование:
    pip install requests trafilatura pandas --break-system-packages
    python uz_sports_parser.py

Результат: uz_corpus_sports_<timestamp>.csv — те же колонки, что и у
остальных файлов корпуса (+ domain='sport', + subcategory).
"""

import time
import csv
import os
import re
import html
from datetime import datetime
from collections import Counter

import requests
import trafilatura
import pandas as pd

# ---------------------------------------------------------------------------
# Цель прогона и что уже собрано
# ---------------------------------------------------------------------------
TARGET_NEW_ARTICLES = 500      # сколько новых статей хотим набрать за прогон
MAX_PAGES_PER_CATEGORY = 60    # защита от бесконечного листания старых страниц
CHECKPOINT_EVERY = 150

EXISTING_CORPUS_FILES = [
    "uz_corpus_merged_final.csv",
]

CATEGORIES = [
    "football", "uzbek_sports", "boxs", "tennis", "solitary",
    "athletics", "weightlifting", "water-sports", "chess",
    "winter-sports", "automobile", "olympism", "others",
]
DOMAIN = "sport"

# ВАЖНО (23.07.2026): переключение на /en/ в прошлой правке было ошибкой —
# /en/ выдаёт англоязычный раздел сайта (первый прогон парсера принёс
# английский текст вместо узбекского). Возвращаемся на /oz/ — это Latin-Uzbek
# раздел (Кириллица — /uz/, латиница — /oz/), что соответствует остальному
# корпусу. per-page=10 из прошлой правки оставляем — это подтверждено по
# /en/-варианту сайта и структура пагинации у языковых разделов одна и та же
# (просто сегмент /en/ меняется на /oz/), так что должно работать так же.
# Если после этого снова придёт 0 кандидатов или неверный язык — проверь
# глазами https://sports.uz/oz/news/football, возможно раздел называется
# иначе или требует другой параметр.
BASE_LIST_URL = "https://sports.uz/oz/news/{category}"
LIST_URL_EXTRA_PARAMS = "&per-page=10"
# Ссылки на статьи в разметке идут БЕЗ языкового префикса
# (https://sports.uz/news/view/<slug>) независимо от того, с какого языкового
# раздела был запрошен листинг — сам slug статьи языконезависим, но текст
# внутри статьи будет на том языке, с раздела которого её загрузили.
ARTICLE_URL_TEMPLATE = "https://sports.uz/news/view/{slug}"

# Раньше: r'href="https://sports\.uz/news/view/([a-z0-9\-]+)"'
# Проблемы старого регэкспа:
#   1) требовал абсолютный домен в href — некоторые ссылки в разметке
#      (например в блоке "Blogs") идут относительными путями "/news/view/...".
#   2) ограничивал slug только [a-z0-9\-], а вживую встречаются slug'и со
#      скобками "(details)" и юникод-тире "–" (пример:
#      fc-fc-sogdiana-draws-1–1-in-friendly-against-kyrgyz-club-abdish-ata).
# Новый вариант: домен опционален, символьный класс расширен под реальные
# slug'и (буквы/цифры/дефис/юникод-буквы/скобки/тире), останавливаясь на
# закрывающей кавычке.
ARTICLE_LINK_RE = re.compile(
    r'href="(?:https://sports\.uz)?/news/view/([^"]+?)"'
)

NEWS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "uz,ru;q=0.9,en;q=0.8",
    "Referer": "https://sports.uz/oz",
}

# Сессия вместо голого requests.get() на каждый вызов — сохраняет cookies
# (в т.ч. возможную CSRF/сессионную куку Yii2) между запросами, как это
# делает обычный браузер при навигации по сайту.
SESSION = requests.Session()
SESSION.headers.update(NEWS_HEADERS)

REQUEST_DELAY_SEC = 1.5
MAX_RETRIES = 3
MIN_TEXT_LENGTH = 200

CHUNK_MIN_WORDS = 100
CHUNK_MAX_WORDS = 200
MIN_TAIL_CHUNK_WORDS = 15


# ---------------------------------------------------------------------------
# Уже собрано — не тащим повторно
# ---------------------------------------------------------------------------
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
# HTTP с повторными попытками
# ---------------------------------------------------------------------------
def fetch_with_retry(url: str, timeout: int = 15):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = SESSION.get(url, timeout=timeout)
            if resp.status_code in (429, 403):
                wait = 5 * attempt
                print(f"     [{resp.status_code}] жду {wait}с (попытка {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                print(f"  [!] Ошибка после {MAX_RETRIES} попыток ({url}): {e}")
                return None
            time.sleep(2 * attempt)
    return None


OG_TITLE_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.IGNORECASE)
TITLE_TAG_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def fetch_article(url: str) -> tuple[str, str]:
    """Возвращает (title, text). title берём из og:title/<title> HTML-страницы,
    а не обрезком текста — так короче и надёжнее, чем гадать по первому предложению."""
    resp = fetch_with_retry(url)
    if resp is None:
        return "", ""

    title = ""
    m = OG_TITLE_RE.search(resp.text)
    if m:
        title = clean_text(m.group(1))
    else:
        m = TITLE_TAG_RE.search(resp.text)
        if m:
            title = clean_text(m.group(1))

    extracted = trafilatura.extract(
        resp.text, include_comments=False, include_tables=False, favor_precision=True,
    )
    text = clean_text(extracted) if extracted else ""
    return title, text


def clean_text(raw_text) -> str:
    if not raw_text:
        return ""
    # html.unescape нужен, т.к. og:title/<title> приходят с HTML-сущностями
    # (&#039;, &quot; и т.п.) — без этого они попадают в корпус как есть,
    # как случилось в первом прогоне (например "women&#039;s national team").
    unescaped = html.unescape(raw_text)
    return re.sub(r"\s+", " ", unescaped).strip()


# ---------------------------------------------------------------------------
# Чанкинг по 100-200 слов (та же логика, что и в остальных парсерах корпуса)
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
# Листинг категории: собираем ссылки на статьи по страницам
# ---------------------------------------------------------------------------
def fetch_category_article_slugs(category: str, max_pages: int,
                                  already_seen_slugs: set[str]) -> list[str]:
    slugs = []
    empty_pages_in_a_row = 0

    for page in range(1, max_pages + 1):
        url = f"{BASE_LIST_URL.format(category=category)}?page={page}{LIST_URL_EXTRA_PARAMS}"
        resp = fetch_with_retry(url)
        time.sleep(REQUEST_DELAY_SEC)

        if resp is None:
            break

        found = [html.unescape(s) for s in ARTICLE_LINK_RE.findall(resp.text)]
        new_on_page = [s for s in dict.fromkeys(found) if s not in slugs and s not in already_seen_slugs]

        if not found:
            # Диагностика: раньше здесь молча считали страницу пустой и шли
            # дальше. Если 0 ссылок нашлось на первой же странице категории —
            # это подозрительно (сравни со счётчиком "уже в корпусе" и с тем,
            # что видно глазами на сайте) и стоит разобраться, а не просто
            # пропускать.
            if page == 1:
                snippet_path = f"debug_{category}_page{page}.html"
                try:
                    with open(snippet_path, "w", encoding="utf-8") as f:
                        f.write(resp.text)
                except OSError:
                    pass
                block_markers = [
                    m for m in ("cf-browser-verification", "Just a moment",
                                "Attention Required", "captcha", "Access denied",
                                "_csrf-frontend")
                    if m.lower() in resp.text.lower()
                ]
                print(f"    [диагностика] 0 ссылок на стр.1 категории '{category}': "
                      f"status={resp.status_code}, длина HTML={len(resp.text)}, "
                      f"сохранено в {snippet_path}. "
                      f"Найденные маркеры: {block_markers or 'нет явных'}")
            empty_pages_in_a_row += 1
            if empty_pages_in_a_row >= 2:
                break
            continue
        empty_pages_in_a_row = 0

        slugs.extend(new_on_page)
        print(f"    стр. {page}: найдено {len(found)} ссылок, новых {len(new_on_page)} "
              f"(всего по категории: {len(slugs)})")

    return slugs


# ---------------------------------------------------------------------------
# Оценка сложности (та же эвристика, что и в остальных парсерах)
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
        return {"avg_sentence_len": 0.0, "avg_word_len": 0.0,
                "rare_word_ratio": 0.0, "complexity_score": 0.0}

    avg_sentence_len = len(words) / len(sentences)
    avg_word_len = sum(len(w) for w in words) / len(words)
    rare_words = sum(1 for w in words if freq_dict.get(w, 0) <= rare_threshold)
    rare_word_ratio = rare_words / len(words)

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
FIELDNAMES = [
    "source", "title", "url", "chunk_id", "text", "word_count", "length",
    "avg_sentence_len", "avg_word_len", "rare_word_ratio", "complexity_score",
    "domain", "subcategory",
]


def save_to_csv(rows: list[dict], filename: str) -> None:
    if not rows:
        print("Нет данных для сохранения.")
        return
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=FIELDNAMES, quoting=csv.QUOTE_ALL,
            lineterminator="\n", extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Сохранено {len(rows)} строк в {filename}")


def build_chunk_records(articles: list[dict]) -> list[dict]:
    chunk_records = []
    for article in articles:
        chunks = chunk_text(article["text"])
        for i, chunk in enumerate(chunks, 1):
            chunk_records.append({
                "source": "sports.uz",
                "title": article["title"],
                "url": article["url"],
                "chunk_id": i,
                "text": chunk,
                "word_count": len(chunk.split()),
                "length": len(chunk),
                "domain": DOMAIN,
                "subcategory": article["subcategory"],
            })
    return chunk_records


def finalize_and_save(all_articles: list[dict], filename: str) -> None:
    print(f"\nСчитаю метрики и сохраняю {len(all_articles)} статей -> {filename}")
    chunk_records = build_chunk_records(all_articles)
    freq_dict = build_frequency_dict([c["text"] for c in chunk_records])
    for chunk in chunk_records:
        chunk.update(compute_complexity(chunk["text"], freq_dict))
    chunk_records.sort(key=lambda c: c["complexity_score"])
    save_to_csv(chunk_records, filename)


def main():
    print("Загружаю список уже собранных URL...")
    existing_urls = load_existing_urls(EXISTING_CORPUS_FILES)
    # slug'и уже собранных статей sports.uz (по обоим вариантам путей,
    # т.к. в корпусе URL мог осесть либо с /oz/, либо без)
    existing_slugs = set()
    for u in existing_urls:
        if "sports.uz" in u:
            existing_slugs.add(u.rstrip("/").split("/")[-1])

    all_articles: list[dict] = []
    seen_slugs: set[str] = set()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    final_filename = f"uz_corpus_sports_{timestamp}.csv"
    checkpoint_filename = f"uz_corpus_sports_{timestamp}_checkpoint.csv"
    last_checkpoint_at = 0

    for category in CATEGORIES:
        if len(all_articles) >= TARGET_NEW_ARTICLES:
            break
        remaining = TARGET_NEW_ARTICLES - len(all_articles)
        print(f"\n== sports.uz: {category} (нужно ещё {remaining}) ==")

        slugs = fetch_category_article_slugs(
            category, MAX_PAGES_PER_CATEGORY, existing_slugs | seen_slugs
        )
        print(f"  Кандидатов из категории: {len(slugs)}")

        for slug in slugs:
            if len(all_articles) >= TARGET_NEW_ARTICLES:
                break
            if slug in seen_slugs or slug in existing_slugs:
                continue
            seen_slugs.add(slug)

            url = ARTICLE_URL_TEMPLATE.format(slug=slug)
            title, text = fetch_article(url)
            time.sleep(REQUEST_DELAY_SEC)

            if len(text) < MIN_TEXT_LENGTH:
                continue

            all_articles.append({
                "title": title,
                "url": url,
                "text": text,
                "subcategory": category,
            })
            print(f"    -> [{len(all_articles)}] {url.split('/')[-1][:55]}")

            if len(all_articles) - last_checkpoint_at >= CHECKPOINT_EVERY:
                finalize_and_save(all_articles, checkpoint_filename)
                last_checkpoint_at = len(all_articles)

        print(f"  Всего новых статей собрано: {len(all_articles)} / {TARGET_NEW_ARTICLES}")

    if not all_articles:
        print("\nНовых статей не найдено.")
        return

    finalize_and_save(all_articles, final_filename)

    if os.path.exists(checkpoint_filename):
        os.remove(checkpoint_filename)

    print(f"\nГОТОВО: {len(all_articles)} новых статей "
          f"({'достигнута' if len(all_articles) >= TARGET_NEW_ARTICLES else 'НЕ достигнута'} цель в {TARGET_NEW_ARTICLES}).")

    sub_counts = Counter(a["subcategory"] for a in all_articles)
    print("\nПо подкатегориям:")
    for sub, cnt in sub_counts.most_common():
        print(f"  {sub}: {cnt}")


if __name__ == "__main__":
    main()
