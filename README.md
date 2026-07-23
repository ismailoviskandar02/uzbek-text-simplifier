# Uzbek Text Simplifier

Упрощение сложных узбекских текстов (юридических, официальных, новостных)
для обычного читателя, с опциональным переводом результата на ru/en.

## Архитектура

1. Parallel-корпус (сложный ↔ простой текст)
2. Fine-tuning mT5-small
3. NLLB-200 — опциональный перевод на ru/en
4. Демо: FastAPI + Streamlit/Gradio, деплой на HuggingFace Spaces

## Структура репозитория

**Сбор данных:**
- `uz_news_parser.py`, `uz_news_parser2.py`, `uz_news_parser3.py` —
  новости (kun.uz, gazeta.uz, uza.uz) + Wikipedia, последовательные
  версии (v3 — актуальная)
- `uz_yur_parser.py` — юридический корпус (lex.uz)
- `uz_sports_parser.py`, `uz_wiki_parser_1000.py`, `uz_wiki_parser_new.py`
  — дополнительный сбор (спорт / расширенная Wikipedia); статус (активно
  используются или legacy-прогоны) уточняется

**`docs/`** — отчёты и документация корпуса:
- `corpus_fields.md` — формулы и описание всех полей датасета
- `merge_report.md` — объединение 4 CSV-прогонов в финальный корпус
- `cleaning_report.md` — отчёт по очистке текста от мусора разметки

**`prompts/`** *(рекомендуется завести)* — инструкции для teacher-модели,
генерирующей parallel-пары:
- инструкция v1 — консервативная (сохранить 85–95% информации)
- инструкция v2 — агрессивная (сократить на 40–60%)

⚠️ Обе версии сейчас в проекте одновременно и задают разные стратегии
упрощения — нужно выбрать одну перед генерацией финальных пар.

## Датасет

CSV-схема: `source, title, url, chunk_id, text, word_count, length,
avg_sentence_len, avg_word_len, rare_word_ratio, complexity_score, domain`

После объединения и дедупа: **2235 чанков**, ~262 500 слов.

| source | строк |
|---|---|
| lex.uz | 1134 |
| wikipedia | 741 |
| gazeta.uz | 127 |
| uza.uz | 125 |
| kun.uz | 108 |

Домены: law 59%, science 9%, economy 7%, остальное (culture/other/
politics/sport/education/technology/health) — по убыванию, каждый <5%.
Подробности — в `docs/merge_report.md`.

## Статус

Корпус собран, объединён и очищен. Дальше: определиться с версией
инструкций для teacher-модели, сгенерировать parallel-пары, fine-tuning
mT5-small.
