"""
Парсер НОВЫХ статей Wikipedia — версия для сбора 1000+ статей за прогон.

Отличия от uz_wiki_parser_new.py (который брал ~120-150 статей на категорию
без пагинации и остановился на ~120 новых статей за прогон):

  1. ПАГИНАЦИЯ через cmcontinue — предыдущая версия читала только первую
     "страницу" categorymembers (до 500 тайтлов), из-за чего из категорий
     с тысячами статей реально попадала лишь верхушка. Теперь ходим по
     cmcontinue, пока не наберём нужный лимит или категория не кончится.
  2. Список категорий расширен темами, которых не хватало в корпусе
     (Qishloq xoʻjaligi, Harbiy ish, Musiqa, Kino), лимит на категорию
     поднят с 120-150 до 250.
  3. Добавлена FALLBACK-категория "Oʻzbekiston milliy ensiklopediyasi
     maqolalari" (~25 600 статей в uz.wikipedia) — она не привязана к одной
     теме, поэтому domain для неё определяется тем же эвристическим
     классификатором по ключевым словам, что использовался при склейке
     корпуса раньше (для файлов без готовой разметки domain). Это резерв
     на случай, если тематических категорий не хватит, чтобы дотянуть до
     TARGET_NEW_ARTICLES.
  4. Есть чекпоинты: каждые CHECKPOINT_EVERY новых статей промежуточный
     результат сохраняется на диск — если скрипт упадёт на статье №800
     из 1000 (сеть, 429 и т.п.), уже собранное не потеряется.
  5. Останавливается, как только набрано TARGET_NEW_ARTICLES новых статей
     (после фильтрации дублей с уже собранным корпусом) — не тратит лишние
     запросы сверх нужного объёма.

Использование:
    pip install requests pandas --break-system-packages
    python uz_wiki_parser_1000.py

Результат: uz_corpus_wiki_1000_<timestamp>.csv (+ промежуточные
uz_corpus_wiki_1000_<timestamp>_checkpoint.csv по пути).
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
# Цель прогона и что уже собрано (чтобы не тащить повторно)
# ---------------------------------------------------------------------------
TARGET_NEW_ARTICLES = 1000   # останавливаемся, набрав столько НОВЫХ статей
CHECKPOINT_EVERY = 200       # промежуточное сохранение каждые N новых статей

EXISTING_CORPUS_FILES = [
    "uz_corpus_merged_final.csv",
    # добавляй сюда все файлы, которые уже входят в корпус, включая
    # предыдущие uz_corpus_wiki_new_*.csv / uz_corpus_wiki_1000_*.csv
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

# Тематические категории с явным доменом — самый надёжный источник разметки.
# Лимит поднят до 250 (было 120-150), плюс пагинация теперь реально достаёт
# такой объём, а не обрезается первой "страницей" API.
CATEGORY_DOMAIN_MAP = {
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
    "Sport": "sport",
    "Informatika": "technology",
    # новые категории для объёма и разнообразия
    "Qishloq xoʻjaligi": "economy",
    "Harbiy ish": "politics",
    "Musiqa": "culture",
    "Kino": "culture",
}
LIMIT_PER_CATEGORY = 250

# Резервная категория без единой темы — используется, только если после
# всех тематических категорий ещё не набралось TARGET_NEW_ARTICLES.
# Домен для статей отсюда определяется классификатором по ключевым словам.
FALLBACK_CATEGORIES = [
    "Oʻzbekiston milliy ensiklopediyasi maqolalari",  # ~25 600 статей
]

WIKI_BATCH_SIZE = 40           # тайтлов за один запрос extracts (макс. 50)
MAX_SUBCAT_DEPTH = 2
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
# Классификатор домена по ключевым словам (для FALLBACK_CATEGORIES) —
# тот же список, что использовался при склейке файлов без готового domain.
# ---------------------------------------------------------------------------
DOMAIN_KEYWORDS = {
    'law': ['қонун','qonun','суд','sud','жиноят','jinoyat','жавобгарлик',
            'javobgarlik','фармон','farmon','қарор','qaror','кодекс','kodeks',
            'модда','modda','ҳуқуқ','huquq','прокуратура','prokuratura',
            'жазо','jazo','қонунчилик','qonunchilik'],
    'science': ['физика','fizika','кимё','kimyo','биология','biologiya','олим','olim',
                'тадқиқот','tadqiqot','илмий','ilmiy','математика','matematika',
                'геология','geologiya','астроном','astronom','kashfiyot','eksperiment'],
    'economy': ['иқтисод','iqtisod','бюджет','budjet','банк','bank','валюта',
                'valyuta','инвестиция','investitsiya','экспорт','eksport','импорт','import',
                'нарх','narx','савдо','savdo','корхона','korxona','солиқ','soliq',
                'биржа','birja','qishloq xoʻjaligi','fermer','hosildorlik'],
    'culture': ['маданият','madaniyat','санъат','sanʼat','кино','kino','музей','muzey',
                'адабиёт','adabiyot','театр','teatr','концерт','konsert','қўшиқ','qoʻshiq',
                'фестиваль','festival','ёзувчи','yozuvchi','рассом','rassom','musiqa'],
    'sport': ['футбол','futbol','чемпионат','chempionat','спортчи','sportchi','олимпиада',
              'olimpiada','мураббий','murabbiy','финал','final','терма жамоа','terma jamoa'],
    'politics': ['президент','prezident','ҳукумат','hukumat','вазир ','vazir ','парламент',
                 'parlament','сенат','senat','сиёсат','siyosat','сайлов','saylov','депутат',
                 'deputat','harbiy','armiya','qoʻshin'],
    'education': ['таълим','taʼlim','мактаб','maktab','университет','universitet',
                  'талаба','talaba','ўқувчи','oʻquvchi','профессор','professor',
                  'лицей','litsey','коллеж','kollej'],
    'health': ['тиббиёт','tibbiyot','касаллик','kasallik','шифокор','shifokor','соғлиқ',
               'sogʻliq','врач','vrach','касалхона','kasalxona','даволаш','davolash',
               'вакцина','vaksina'],
    'technology': ['технология','texnologiya','дастур','dastur','сунъий интеллект',
                   "sunʼiy intellekt","sun'iy intellekt",'компьютер','kompyuter','интернет',
                   'internet','робот','robot','рақамли','raqamli'],
}


def classify_by_keywords(title: str, text: str) -> str:
    combined = f"{title} {text}".lower()
    scores = {}
    for dom, kws in DOMAIN_KEYWORDS.items():
        c = sum(combined.count(kw.lower()) for kw in kws)
        if c:
            scores[dom] = c
    if not scores:
        return "other"
    return max(scores, key=scores.get)


# ---------------------------------------------------------------------------
# Очистка текста
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
# Чанкинг по 100-200 слов
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
def fetch_with_retry(url: str, params: dict = None, timeout: int = 15):
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
# Список статей категории — теперь С ПАГИНАЦИЕЙ (cmcontinue), а не только
# первой "страницей" ответа API. Это и есть главное отличие от предыдущей
# версии, из-за которого раньше объём упирался в потолок ~120 на категорию.
# ---------------------------------------------------------------------------
def fetch_category_member_titles(category: str, limit: int,
                                  max_subcat_depth: int = MAX_SUBCAT_DEPTH) -> list[str]:
    titles: list[str] = []
    subcats_to_expand: list[str] = []
    cmcontinue = None

    while len(titles) < limit:
        list_params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Turkum:{category}",
            "cmlimit": min(limit - len(titles), 500),
            "format": "json",
        }
        if cmcontinue:
            list_params["cmcontinue"] = cmcontinue

        resp = fetch_with_retry(WIKI_API_URL, params=list_params)
        if resp is None:
            break

        try:
            data = resp.json()
        except ValueError as e:
            print(f"  [!] Ошибка разбора ответа для категории {category}: {e}")
            break

        members = data.get("query", {}).get("categorymembers", [])
        for m in members:
            title = m["title"]
            if title.startswith("Turkum:"):
                subcats_to_expand.append(title.removeprefix("Turkum:"))
            else:
                titles.append(title)

        cmcontinue = data.get("continue", {}).get("cmcontinue")
        if not cmcontinue:
            break
        time.sleep(WIKI_DELAY_SEC)

    if len(titles) < limit and max_subcat_depth > 0:
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


def fetch_articles_for_titles(titles: list[str], domain_fn, existing_urls: set[str],
                               seen_titles: set[str], remaining_needed: int) -> list[dict]:
    """
    domain_fn: либо строка-константа (тематическая категория), либо функция
    (title, text) -> domain (для FALLBACK-категорий).
    Останавливается досрочно, как только набрано remaining_needed статей.
    """
    new_titles = [
        t for t in titles
        if t not in seen_titles
        and f"https://uz.wikipedia.org/wiki/{t.replace(' ', '_')}" not in existing_urls
    ]
    print(f"  Тайтлов: {len(titles)}, уже в корпусе/дублей: {len(titles) - len(new_titles)}, "
          f"новых кандидатов: {len(new_titles)}")

    articles = []
    for batch in chunk_list(new_titles, WIKI_BATCH_SIZE):
        if len(articles) >= remaining_needed:
            break

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

            domain = domain_fn if isinstance(domain_fn, str) else domain_fn(title, text)

            articles.append({
                "source": "wikipedia",
                "title": title,
                "url": url,
                "text": text,
                "length": len(text),
                "domain": domain,
            })
            print(f"    -> [{len(articles)}] {title[:55]}  ({domain})")

    return articles


# ---------------------------------------------------------------------------
# Сложность (та же эвристика, что и в остальных парсерах корпуса)
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
    "domain",
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
                "source": article["source"],
                "title": article["title"],
                "url": article["url"],
                "chunk_id": i,
                "text": chunk,
                "word_count": len(chunk.split()),
                "length": len(chunk),
                "domain": article["domain"],
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

    all_articles: list[dict] = []
    seen_titles: set[str] = set()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    final_filename = f"uz_corpus_wiki_1000_{timestamp}.csv"
    checkpoint_filename = f"uz_corpus_wiki_1000_{timestamp}_checkpoint.csv"
    last_checkpoint_at = 0

    # --- 1. Тематические категории (точный domain) ---
    for category, domain in CATEGORY_DOMAIN_MAP.items():
        if len(all_articles) >= TARGET_NEW_ARTICLES:
            break
        remaining = TARGET_NEW_ARTICLES - len(all_articles)
        print(f"\n== Wikipedia: {category} -> domain={domain} "
              f"(нужно ещё {remaining}) ==")
        titles = fetch_category_member_titles(category, LIMIT_PER_CATEGORY)
        new_articles = fetch_articles_for_titles(
            titles, domain, existing_urls, seen_titles, remaining
        )
        all_articles.extend(new_articles)
        print(f"  Всего новых статей собрано: {len(all_articles)} / {TARGET_NEW_ARTICLES}")

        if len(all_articles) - last_checkpoint_at >= CHECKPOINT_EVERY:
            finalize_and_save(all_articles, checkpoint_filename)
            last_checkpoint_at = len(all_articles)

    # --- 2. Резервные категории (domain через классификатор по ключевым словам) ---
    if len(all_articles) < TARGET_NEW_ARTICLES:
        print(f"\nПосле тематических категорий: {len(all_articles)} статей, "
              f"нужно ещё {TARGET_NEW_ARTICLES - len(all_articles)} -> "
              f"подключаю резервные категории.")
        for category in FALLBACK_CATEGORIES:
            if len(all_articles) >= TARGET_NEW_ARTICLES:
                break
            remaining = TARGET_NEW_ARTICLES - len(all_articles)
            print(f"\n== Wikipedia (fallback): {category} (нужно ещё {remaining}) ==")
            titles = fetch_category_member_titles(category, remaining * 2)  # с запасом на дубли/короткие статьи
            new_articles = fetch_articles_for_titles(
                titles, classify_by_keywords, existing_urls, seen_titles, remaining
            )
            all_articles.extend(new_articles)
            print(f"  Всего новых статей собрано: {len(all_articles)} / {TARGET_NEW_ARTICLES}")

            if len(all_articles) - last_checkpoint_at >= CHECKPOINT_EVERY:
                finalize_and_save(all_articles, checkpoint_filename)
                last_checkpoint_at = len(all_articles)

    if not all_articles:
        print("\nНовых статей не найдено.")
        return

    finalize_and_save(all_articles, final_filename)

    if os.path.exists(checkpoint_filename):
        os.remove(checkpoint_filename)

    print(f"\nГОТОВО: {len(all_articles)} новых статей "
          f"({'достигнута' if len(all_articles) >= TARGET_NEW_ARTICLES else 'НЕ достигнута'} цель в {TARGET_NEW_ARTICLES}).")

    domain_counts = Counter(a["domain"] for a in all_articles)
    print("\nПо доменам (статьи, не чанки):")
    for dom, cnt in domain_counts.most_common():
        print(f"  {dom}: {cnt}")


if __name__ == "__main__":
    main()
