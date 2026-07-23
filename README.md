# Uzbek Text Simplifier

Упрощение сложных узбекских текстов (юридических, официальных, новостных)
для обычного читателя, с опциональным переводом результата на ru/en.

## Архитектура

1. Parallel-корпус (сложный ↔ простой текст)
2. Fine-tuning mT5-small
3. NLLB-200 — опциональный перевод на ru/en
4. Демо: FastAPI + Streamlit/Gradio, деплой на HuggingFace Spaces

## Структура репозитория

- `uz_news_parser.py`, `uz_news_parser2.py`, `uz_news_parser3.py` —
  парсеры новостей (kun.uz, gazeta.uz, uza.uz) и Wikipedia
- `uz_yur_parser.py` — парсер юридических текстов (lex.uz)
- `clean_corpus.py` — постобработка и очистка объединённого CSV
- `corpus_fields.md` — формулы и описание всех полей датасета
- `cleaning_report.txt`, `domain_report.txt` — отчёты по очистке и доменам

## Датасет

CSV-схема: `source, title, url, chunk_id, text, word_count, length,
avg_sentence_len, avg_word_len, rare_word_ratio, complexity_score, domain`

Источники: новости (kun.uz, gazeta.uz, uza.uz), Wikipedia, lex.uz
(законодательство РУз). 2033 чанка из 230 статей.

## Статус

Корпус собран и очищен. Дальше: генерация parallel-пар (сложный/простой)
через teacher-модель и fine-tuning mT5-small.
