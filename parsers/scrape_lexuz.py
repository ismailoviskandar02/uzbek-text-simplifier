"""
Парсер актов с lex.uz (Национальная база данных законодательства РУз).

ОБНОВЛЕНО по реальной вёрстке (проверено вручную на живой странице
https://lex.uz/uz/docs/-6118102):

- URL документа: https://lex.uz/uz/docs/{id}, где id — обычно ОТРИЦАТЕЛЬНОЕ
  число (это нормально, так у них устроена система, не баг). Пример
  реального документа: https://lex.uz/uz/docs/-6118102
- sitemap.xml НЕ существует (редиректит на /Pages/404.aspx) — этот вариант
  из старой версии скрипта убран.
- Текст документа целиком лежит в <div id="divCont"> — внутри много
  вложенных <div class="ACT_TEXT">/<div class="ACT_TITLE">/... — не нужно
  парсить их по отдельности, просто берём .get_text() всего divCont.
- Заголовок — самый надёжный источник: тег <title>.
- 404 определяется по отсутствию div#divCont в ответе (плюс возможен
  реальный HTTP 404 на несуществующий id).

СТРАТЕГИЯ СБОРА — BFS по внутренним ссылкам, а не перебор id подряд:
Каждый документ на lex.uz ссылается на другие документы через
<a href="/uz/docs/-XXXXXXX">. Это даёт естественный способ находить новые
валидные id, НЕ дёргая сервер тысячами запросов на несуществующие страницы
(что более уважительно к сайту, чем прямой перебор диапазона).

Нужны стартовые (seed) id — возьми их из уже собранного raw_dataset.csv
(колонка url), там уже есть под тысячу реальных id.
"""

import re
import sys
import csv
from collections import deque
from bs4 import BeautifulSoup
from common import polite_get, check_robots, JsonlStore, HEADERS
import requests

BASE = "https://lex.uz"
OUT_PATH = "lexuz_acts.jsonl"

DOC_LINK_RE = re.compile(r"/uz/docs/(-?\d+)")


def parse_doc(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")

    body_tag = soup.find("div", id="divCont")
    if body_tag is None:
        return None  # не документ / 404

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    body = body_tag.get_text("\n", strip=True)
    if not body:
        return None

    linked_ids = set(DOC_LINK_RE.findall(html))

    return {"title": title, "text": body, "linked_ids": list(linked_ids)}


def load_seed_ids_from_raw_dataset(csv_path: str) -> list[str]:
    ids = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get("url", "")
            m = DOC_LINK_RE.search(url) or re.search(r"/docs/(-?\d+)", url)
            if m:
                ids.append(m.group(1))
    return ids


def scrape_bfs(seed_ids, max_docs=5000, out_path=OUT_PATH):
    if not check_robots(BASE, "/uz/docs/-1"):
        print("robots.txt запрещает доступ к этому пути. Останавливаюсь.")
        sys.exit(1)

    store = JsonlStore(out_path)
    session = requests.Session()
    session.headers.update(HEADERS)

    queue = deque(seed_ids)
    queued = set(seed_ids)

    while queue and len(store) < max_docs:
        doc_id = queue.popleft()
        if store.has(doc_id):
            continue

        url = f"{BASE}/uz/docs/{doc_id}"
        try:
            resp = polite_get(url, session=session)
        except Exception as e:
            print(f"[skip] id={doc_id}: {e}")
            continue

        parsed = parse_doc(resp.text)
        if parsed is None:
            print(f"[нет документа] id={doc_id}")
            continue

        linked_ids = parsed.pop("linked_ids")
        record = {"id": doc_id, "url": url, **parsed}
        store.append(record)

        for lid in linked_ids:
            if lid not in queued:
                queued.add(lid)
                queue.append(lid)

        if len(store) % 50 == 0:
            print(f"Собрано документов: {len(store)} (в очереди: {len(queue)})")

    print(f"Готово. Всего собрано: {len(store)} -> {out_path}")


if __name__ == "__main__":
    # Вариант А (рекомендуется): взять seed id из уже собранного raw_dataset.csv
    # Положи raw_dataset.csv рядом со скриптом и раскомментируй:
    #
    # seeds = load_seed_ids_from_raw_dataset("raw_dataset.csv")
    # print(f"Загружено {len(seeds)} стартовых id из raw_dataset.csv")

    # Вариант Б: вручную заданные id для старта (реально существуют на сайте,
    # проверено вручную)
    seeds = ["-6118102", "-4396428", "245006", "-6212581", "-1727490"]

    scrape_bfs(seeds, max_docs=5000)
