"""
Парсер юридических текстов с lex.uz (Национальная база данных
законодательства Узбекистана) для корпуса text simplification.

lex.uz — тяжёлое JS-приложение с AJAX-поиском, без публичного sitemap/API,
поэтому автоматический обход "всех документов" здесь не сделан (это отдельная,
куда более хрупкая задача — реверс-инжиниринг их поискового AJAX-эндпоинта).
Вместо этого — список конкретных, вручную проверенных документов (Конституция,
основные кодексы и законы), которые дают настоящий канцелярско-юридический
стиль текста. Список в SEED_DOCS можно и нужно расширять вручную: зайди на
lex.uz/uz/, найди нужный документ через поиск, скопируй его URL вида
lex.uz/docs/{id} или lex.uz/uz/docs/{id} и добавь строкой в SEED_DOCS ниже.

Схема выходного CSV идентична uz_news_parser.py (source, title, url, chunk_id,
text, word_count, length, avg_sentence_len, avg_word_len, rare_word_ratio,
complexity_score) — специально, чтобы можно было напрямую склеить оба корпуса.

Использование:
    pip install requests trafilatura --break-system-packages
    python uz_legal_parser.py
"""

import time
import csv
import re
from datetime import datetime
from collections import Counter

import requests
import trafilatura

# ---------------------------------------------------------------------------
# Список документов (вручную проверенные, реально существующие URL)
# ---------------------------------------------------------------------------
SEED_DOCS = [
    ("https://lex.uz/docs/-6445145", "O'zbekiston Respublikasi Konstitutsiyasi"),
    ("https://lex.uz/docs/-104720", "Oila kodeksi"),
    ("https://lex.uz/docs/-142859", "Mehnat kodeksi"),
    ("https://lex.uz/docs/-5534923", "Sudlar to'g'risida"),
    ("https://lex.uz/docs/-5378966", "Normativ-huquqiy hujjatlar to'g'risida"),
    ("https://lex.uz/docs/-4761984", "O'zbekiston Respublikasining fuqaroligi to'g'risida"),
    ("https://lex.uz/docs/-4646908", "Maktabgacha ta'lim va tarbiya to'g'risida"),
    ("https://lex.uz/docs/-3031427", "Mehnatni muhofaza qilish to'g'risida"),
    ("https://lex.uz/docs/-121051", "Davlat tili haqida"),
    # Добавляй сюда новые (url, title) пары по мере расширения списка.
    # Кандидаты, которые стоит поискать и добавить вручную (не проверены):
    # Fuqarolik kodeksi, Jinoyat kodeksi, Soliq kodeksi, Yer kodeksi,
    # Ma'muriy javobgarlik to'g'risidagi kodeks, Uy-joy kodeksi,
    # Iste'molchilar huquqlarini himoya qilish to'g'risida,
    # Tadbirkorlik faoliyati erkinligining kafolatlari to'g'risida.
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uz,ru;q=0.9,en;q=0.8",
}

REQUEST_DELAY_SEC = 3.0  # чуть больше, чем у новостей — документы тяжелее, сайт один
MAX_RETRIES = 3
MIN_TEXT_LENGTH = 500  # юридический документ короче 500 символов — подозрительно мало

# ---------------------------------------------------------------------------
# Очистка мусора, специфичного для lex.uz
# ---------------------------------------------------------------------------
# Этот паттерн (на кириллице, даже когда сам текст закона на латинице!) —
# UI-виджет "отправить предложение по документу / прослушать аудио / получить
# ссылку", вставлен перед КАЖДЫМ абзацем почти на каждой странице lex.uz.
# Проверено вручную на нескольких документах (Sudlar to'g'risida, Mehnat
# kodeksi, Konstitutsiya) — паттерн стабилен, просто regex.sub с ним.
LEX_UI_NOISE_RE = re.compile(
    r"Ҳужжатга\s*таклиф\s*юборишАудиони\s*тинглашҲужжат\s*элементидан\s*ҳавола\s*олиш"
)

# "См. предыдущую/следующую редакцию" — служебные ссылки навигации по
# редакциям документа, не часть юридического текста.
LEX_NAV_NOISE_RE = re.compile(r"\b(Oldingi|Keyingi) tahrirga qarang\.")

# Если увидишь после первого прогона, что в текст всё ещё просачивается
# сайдбар (например "Асосий реквизитлар Кодификация ..." или блок соцсетей
# "Улашиш Telegram Facebook..."), пришли мне пример строки — добавлю сюда.


def clean_text(raw_text: str) -> str:
    if not raw_text:
        return ""
    text = re.sub(r"\s+", " ", raw_text)
    return text.strip()


def strip_lex_noise(text: str) -> str:
    text = LEX_UI_NOISE_RE.sub(" ", text)
    text = LEX_NAV_NOISE_RE.sub(" ", text)
    return clean_text(text)


# ---------------------------------------------------------------------------
# Разбивка на чанки 100-200 слов — идентична uz_news_parser.py, чтобы схема
# и характеристики чанков были сопоставимы между источниками корпуса.
# ---------------------------------------------------------------------------
CHUNK_MIN_WORDS = 100
CHUNK_MAX_WORDS = 200
MIN_TAIL_CHUNK_WORDS = 15


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
        sentence_word_count = len(sentence.split())
        current_sentences.append(sentence)
        current_word_count += sentence_word_count

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
# Оценка сложности — идентична uz_news_parser.py
# ---------------------------------------------------------------------------
WORD_RE = re.compile(r"[a-zA-Zʻʼ'\u0400-\u04FF]+")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


def tokenize_words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]


def build_frequency_dict(all_texts: list[str]) -> Counter:
    counter = Counter()
    for text in all_texts:
        counter.update(tokenize_words(text))
    return counter


def compute_complexity(text: str, freq_dict: Counter, rare_threshold: int = 3) -> dict:
    words = tokenize_words(text)
    sentences = split_sentences(text)

    if not words or not sentences:
        return {"avg_sentence_len": 0.0, "rare_word_ratio": 0.0,
                "avg_word_len": 0.0, "complexity_score": 0.0}

    avg_sentence_len = len(words) / len(sentences)
    avg_word_len = sum(len(w) for w in words) / len(words)

    rare_count = sum(1 for w in words if freq_dict.get(w, 0) <= rare_threshold)
    rare_word_ratio = rare_count / len(words)

    norm_sentence_len = min(avg_sentence_len / 25, 1.0)
    norm_word_len = min(avg_word_len / 10, 1.0)
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


# ---------------------------------------------------------------------------
# Загрузка документов
# ---------------------------------------------------------------------------
def fetch_with_retry(url: str, timeout: int = 15):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
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


def fetch_legal_doc(url: str, title: str) -> dict | None:
    print(f"  -> {title}")
    resp = fetch_with_retry(url)
    if resp is None:
        return None

    extracted = trafilatura.extract(
        resp.text,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not extracted:
        print("     [пропущено: trafilatura не нашла основной текст]")
        return None

    text = strip_lex_noise(clean_text(extracted))
    if len(text) < MIN_TEXT_LENGTH:
        print(f"     [пропущено: текст короче {MIN_TEXT_LENGTH} символов после очистки — "
              f"возможно, trafilatura не смогла выделить контент]")
        return None

    return {
        "source": "lex.uz",
        "title": title,
        "url": url,
        "text": text,
        "length": len(text),
    }


def build_chunk_records(docs: list[dict]) -> list[dict]:
    chunk_records = []
    for doc in docs:
        chunks = chunk_text(doc["text"])
        for i, chunk in enumerate(chunks, 1):
            chunk_records.append(
                {
                    "source": doc["source"],
                    "title": doc["title"],
                    "url": doc["url"],
                    "chunk_id": i,
                    "text": chunk,
                    "word_count": len(chunk.split()),
                    "length": len(chunk),
                }
            )
    return chunk_records


def save_to_csv(rows: list[dict], fieldnames: list[str], filename: str) -> None:
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

    # та же самопроверка чтения, что и в uz_news_parser.py
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


def main():
    print(f"Документов в списке: {len(SEED_DOCS)}")
    docs = []
    for url, title in SEED_DOCS:
        doc = fetch_legal_doc(url, title)
        if doc:
            docs.append(doc)
        time.sleep(REQUEST_DELAY_SEC)

    if not docs:
        print("Ничего не собрано — проверь доступность lex.uz и список SEED_DOCS.")
        return

    print(f"\nУспешно загружено {len(docs)} из {len(SEED_DOCS)} документов.")
    print("Разбиваю на чанки по 100-200 слов...")
    chunk_records = build_chunk_records(docs)
    print(f"Получено {len(chunk_records)} чанков из {len(docs)} документов.")

    print("Строю частотный словарь по корпусу...")
    freq_dict = build_frequency_dict([c["text"] for c in chunk_records])

    for chunk in chunk_records:
        metrics = compute_complexity(chunk["text"], freq_dict)
        chunk.update(metrics)

    chunk_records.sort(key=lambda c: c["complexity_score"])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    fieldnames = [
        "source", "title", "url", "chunk_id", "text", "word_count", "length",
        "avg_sentence_len", "avg_word_len", "rare_word_ratio", "complexity_score",
    ]
    save_to_csv(chunk_records, fieldnames, filename=f"uz_legal_corpus_{timestamp}.csv")

    scores = [c["complexity_score"] for c in chunk_records]
    print(f"\nДиапазон сложности: {min(scores):.3f} — {max(scores):.3f}")
    print(f"Медиана: {sorted(scores)[len(scores)//2]:.3f}")

    # Быстрая проверка: не остался ли мусор lex.uz в тексте после чистки.
    leftover = sum(1 for c in chunk_records if "таклиф" in c["text"].lower()
                   or "тахрирга қаранг" in c["text"].lower())
    if leftover:
        print(f"\n[!] {leftover} чанков всё ещё содержат подозрение на мусор lex.uz — "
              f"загляни в них вручную, возможно нужно расширить LEX_UI_NOISE_RE.")


if __name__ == "__main__":
    main()