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
- `uz_wiki_parser_1000.py`, `uz_wiki_parser_new.py` — дополнительные
  прогоны Wikipedia; **статус подтверждён**, результат уже включён в
  объединённый корпус (`uz_corpus_merged_final_fixed.csv`)
- `uz_sports_parser.py` — спортивный корпус; статус отдельный, **пока
  не входит** в основной объединённый файл (своя схема полей с
  `subcategory`, свой файл `uz_corpus_sports_*.csv`)

**`docs/`** — отчёты и документация корпуса:
- `corpus_fields.md` — формулы и описание всех полей датасета
- `merge_report.md` — объединение прогонов в финальный корпус
  (обновлено: включает починку повреждённого CSV-экспорта и два
  дополнительных Wikipedia-прогона)
- `cleaning_report.md` — отчёт по очистке текста от мусора разметки

**`prompts/`** *(рекомендуется завести)* — инструкции для teacher-модели,
генерирующей parallel-пары:
- инструкция v1 — консервативная (сохранить 85–95% информации)
- инструкция v2 — агрессивная (сократить на 40–60%)

⚠️ Обе версии сейчас в проекте одновременно и задают разные стратегии
упрощения — нужно выбрать одну перед генерацией финальных пар.

## Датасет

CSV-схема: `source, title, url, chunk_id, text, word_count, length,
avg_sentence_len, avg_word_len, rare_word_ratio, complexity_score,
domain, domain_filled_auto, origin_file`

Файл: **`uz_corpus_merged_final_fixed.csv`**. После объединения и
дедупа: **2572 чанка**, ~297 000 слов.

| source | строк |
|---|---|
| wikipedia | 1149 |
| lex.uz | 1071 |
| gazeta.uz | 124 |
| uza.uz | 124 |
| kun.uz | 104 |

Домены: law ~50%, science ~11%, economy ~7%, culture ~7%, sport ~6%,
остальное (politics/other/technology/health/education) — по убыванию,
каждый <5%. Подробности — в `docs/merge_report.md`.

## Статус

Корпус собран, объединён и очищен; ранее повреждённый CSV-экспорт
объединённого файла починен (была лишняя обёртка CSV-кавычек в каждой
строке — см. `docs/merge_report.md`). Дальше: определиться с версией
инструкций для teacher-модели, сгенерировать parallel-пары, fine-tuning
mT5-small.
