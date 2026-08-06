---
title: Uzbek Text Simplifier
emoji: 🇺🇿
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
---

# Упрощение узбекских текстов

Fine-tuned mT5-small — упрощает сложные (юридические, официальные) узбекские тексты.

Модель: [ismailoviskandar02/uzbek-text-simplifier](https://huggingface.co/ismailoviskandar02/uzbek-text-simplifier)

## Структура папки

```
app.py
requirements.txt
```

Веса модели грузятся автоматически с Hugging Face Hub, локальная папка `model/` не нужна.

```
pip install -U -r requirements.txt
python app.py
```
