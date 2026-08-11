"""
Uzbek Text Simplifier — Gradio demo.

Модель грузится напрямую с Hugging Face Hub (репозиторий MODEL_ID),
поэтому веса заливать в Space вручную не нужно — при первом запуске
они скачаются автоматически и закешируются.

Запуск локально:
    pip install -r requirements.txt
    python app.py

Деплой на HuggingFace Spaces:
    1. Создать Space (SDK: Gradio)
    2. Залить app.py, requirements.txt, README.md
    3. Spaces сам соберёт и запустит (весов заливать не нужно)
"""

import gradio as gr
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
import torch

MODEL_ID = "ismailoviskandar02/uzbek-text-simplifier"  # твой репозиторий модели на HF
PREFIX = "simplify: "
MAX_INPUT_LEN = 256
MAX_NEW_TOKENS = 256

device = "cuda" if torch.cuda.is_available() else "cpu"

print(f"Loading model {MODEL_ID} on {device}...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, subfolder="model")
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID, subfolder="model").to(device)
model.eval()
print("Model loaded.")


def simplify(text: str, num_beams: int = 4) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    inputs = tokenizer(
        PREFIX + text,
        return_tensors="pt",
        truncation=True,
        max_length=MAX_INPUT_LEN,
    ).to(device)

    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            num_beams=num_beams,
        )

    return tokenizer.decode(out[0], skip_special_tokens=True)


# ---------------------------------------------------------------------------
# Локализация (RU / UZ / EN)
# ---------------------------------------------------------------------------

LANGS = {
    "ru": {
        "flag": "🇷🇺",
        "name": "Русский",
        "title_md": """
        # 🇺🇿 Oʻzbek matnini soddalashtirish
        ### Упрощение узбекского текста с помощью нейросети

        Переводит сложный текст (юридический, официальный, государственный документ)
        в понятный, простой узбекский язык.

        _Uzbek Text Simplifier — fine-tuned mT5-small_
        """,
        "input_label": "Исходный текст",
        "input_placeholder": "Вставьте сложный узбекский текст сюда...",
        "beams_label": "Beam search (качество vs скорость)",
        "submit": "✨ Упростить",
        "clear": "🗑️ Очистить",
        "output_label": "Упрощённый текст",
        "examples_label": "Примеры",
        "footer_md": "Сделано с ❤️ для узбекского языка",
    },
    "uz": {
        "flag": "🇺🇿",
        "name": "Oʻzbek tili",
        "title_md": """
        # 🇺🇿 Oʻzbek matnini soddalashtirish
        ### Sun'iy intellekt yordamida matnni soddalashtirish

        Murakkab matnni (huquqiy, rasmiy, davlat hujjati) tushunarli,
        oddiy oʻzbek tiliga oʻgiradi.

        _Uzbek Text Simplifier — fine-tuned mT5-small_
        """,
        "input_label": "Asl matn",
        "input_placeholder": "Murakkab oʻzbek matnini shu yerga joylashtiring...",
        "beams_label": "Beam search (sifat vs tezlik)",
        "submit": "✨ Soddalashtirish",
        "clear": "🗑️ Tozalash",
        "output_label": "Soddalashtirilgan matn",
        "examples_label": "Namunalar",
        "footer_md": "Oʻzbek tili uchun ❤️ bilan yaratilgan",
    },
    "en": {
        "flag": "🇬🇧",
        "name": "English",
        "title_md": """
        # 🇺🇿 Uzbek Text Simplifier
        ### Simplify Uzbek text with AI

        Turns complex text (legal, official, government documents)
        into clear, simple Uzbek.

        _Uzbek Text Simplifier — fine-tuned mT5-small_
        """,
        "input_label": "Original text",
        "input_placeholder": "Paste complex Uzbek text here...",
        "beams_label": "Beam search (quality vs speed)",
        "submit": "✨ Simplify",
        "clear": "🗑️ Clear",
        "output_label": "Simplified text",
        "examples_label": "Examples",
        "footer_md": "Made with ❤️ for the Uzbek language",
    },
}

EXAMPLES = [
    ["Oʻzbekiston Respublikasi Vazirlar Mahkamasining qarori bilan tasdiqlangan Nizomga muvofiq, davlat organlari oʻz vakolatlari doirasida tegishli chora-tadbirlarni amalga oshiradilar."],
    ["Ushbu shartnoma tomonlar oʻrtasida oʻzaro kelishilgan holda tuzilgan boʻlib, unga muvofiq har bir tomon oʻz majburiyatlarini belgilangan muddatlarda bajarishi shart."],
]

# ---------------------------------------------------------------------------
# Тёмная сине-чёрная тема
# ---------------------------------------------------------------------------

THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.blue,
    secondary_hue=gr.themes.colors.slate,
    neutral_hue=gr.themes.colors.slate,
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "sans-serif"],
).set(
    body_background_fill="#05070d",
    body_background_fill_dark="#05070d",
    background_fill_primary="#0b0f1a",
    background_fill_primary_dark="#0b0f1a",
    background_fill_secondary="#0f1524",
    background_fill_secondary_dark="#0f1524",
    border_color_primary="#1e2740",
    border_color_primary_dark="#1e2740",
    block_background_fill="#0b0f1a",
    block_background_fill_dark="#0b0f1a",
    block_border_color="#1e2740",
    block_border_color_dark="#1e2740",
    block_label_background_fill="#141b2e",
    block_label_background_fill_dark="#141b2e",
    block_label_text_color="#8fb3ff",
    block_label_text_color_dark="#8fb3ff",
    block_title_text_color="#e6edff",
    block_title_text_color_dark="#e6edff",
    body_text_color="#dbe4ff",
    body_text_color_dark="#dbe4ff",
    body_text_color_subdued="#7d8bb3",
    body_text_color_subdued_dark="#7d8bb3",
    input_background_fill="#0d1220",
    input_background_fill_dark="#0d1220",
    input_border_color="#243257",
    input_border_color_dark="#243257",
    input_border_color_focus="#3b6bff",
    input_border_color_focus_dark="#3b6bff",
    button_primary_background_fill="linear-gradient(90deg, #2952e3, #3b6bff)",
    button_primary_background_fill_dark="linear-gradient(90deg, #2952e3, #3b6bff)",
    button_primary_background_fill_hover="linear-gradient(90deg, #3b6bff, #5b8bff)",
    button_primary_background_fill_hover_dark="linear-gradient(90deg, #3b6bff, #5b8bff)",
    button_primary_text_color="#ffffff",
    button_primary_text_color_dark="#ffffff",
    button_secondary_background_fill="#141b2e",
    button_secondary_background_fill_dark="#141b2e",
    button_secondary_text_color="#c3cdf0",
    button_secondary_text_color_dark="#c3cdf0",
    button_secondary_border_color="#243257",
    button_secondary_border_color_dark="#243257",
    slider_color="#3b6bff",
    slider_color_dark="#3b6bff",
    shadow_drop="0 4px 20px rgba(0,0,0,0.5)",
)

CUSTOM_CSS = """
#app-header {
    text-align: center;
    padding: 8px 0 4px 0;
}
#lang-row {
    display: flex;
    justify-content: flex-end;
    margin-bottom: -8px;
}
#lang-picker {
    max-width: 180px;
}
.gradio-container {
    background: radial-gradient(circle at 20% -10%, #101c3d 0%, #05070d 45%) !important;
}
footer {display: none !important;}
#footer-note {
    text-align: center;
    color: #5f6d94;
    font-size: 0.85em;
    padding-top: 10px;
}
"""


def on_simplify(text, beams):
    return simplify(text, beams)


def on_language_change(lang_code):
    t = LANGS[lang_code]
    return (
        gr.update(value=t["title_md"]),
        gr.update(label=t["input_label"], placeholder=t["input_placeholder"]),
        gr.update(label=t["beams_label"]),
        gr.update(value=t["submit"]),
        gr.update(value=t["clear"]),
        gr.update(label=t["output_label"]),
        gr.update(value=f'<div id="footer-note">{t["footer_md"]}</div>'),
    )


def _gradio_major_version() -> int:
    try:
        return int(gr.__version__.split(".")[0])
    except Exception:
        return 4


_GR_MAJOR = _gradio_major_version()

# В Gradio >= 6 theme/css передаются в launch(), а не в Blocks().
_blocks_kwargs = {"title": "Oʻzbek matnini soddalashtirish"}
if _GR_MAJOR < 6:
    _blocks_kwargs["theme"] = THEME
    _blocks_kwargs["css"] = CUSTOM_CSS

with gr.Blocks(**_blocks_kwargs) as demo:
    default_lang = "ru"
    t0 = LANGS[default_lang]

    with gr.Row(elem_id="lang-row"):
        lang_picker = gr.Dropdown(
            choices=[(f"{v['flag']} {v['name']}", k) for k, v in LANGS.items()],
            value=default_lang,
            show_label=False,
            container=False,
            elem_id="lang-picker",
        )

    title_md = gr.Markdown(t0["title_md"], elem_id="app-header")

    with gr.Row():
        with gr.Column():
            input_box = gr.Textbox(
                label=t0["input_label"],
                placeholder=t0["input_placeholder"],
                lines=10,
            )
            beams_slider = gr.Slider(
                minimum=1, maximum=8, value=4, step=1,
                label=t0["beams_label"],
            )
            with gr.Row():
                clear_btn = gr.Button(t0["clear"], variant="secondary")
                submit_btn = gr.Button(t0["submit"], variant="primary")

        with gr.Column():
            output_box = gr.Textbox(
                label=t0["output_label"],
                lines=10,
                interactive=False,
            )

    gr.Examples(examples=EXAMPLES, inputs=input_box, label=t0["examples_label"])

    footer_note = gr.HTML(f'<div id="footer-note">{t0["footer_md"]}</div>')

    # события
    submit_btn.click(fn=on_simplify, inputs=[input_box, beams_slider], outputs=output_box)
    input_box.submit(fn=on_simplify, inputs=[input_box, beams_slider], outputs=output_box)
    clear_btn.click(fn=lambda: ("", ""), inputs=None, outputs=[input_box, output_box])

    lang_picker.change(
        fn=on_language_change,
        inputs=lang_picker,
        outputs=[title_md, input_box, beams_slider, submit_btn, clear_btn, output_box, footer_note],
    )

if __name__ == "__main__":
    # В Gradio >= 6 theme/css передаются в launch(), а не в Blocks().
    launch_kwargs = {"server_name": "127.0.0.1", "server_port": 7860}
    if _GR_MAJOR >= 6:
        launch_kwargs["theme"] = THEME
        launch_kwargs["css"] = CUSTOM_CSS

    try:
        demo.launch(**launch_kwargs)
    except ValueError:
        # На некоторых Windows-конфигурациях localhost блокируется прокси/файрволом —
        # тогда gradio создаёт временную публичную ссылку (*.gradio.live).
        print("Локальный доступ недоступен, создаю публичную ссылку...")
        share_kwargs = {"share": True}
        if _GR_MAJOR >= 6:
            share_kwargs["theme"] = THEME
            share_kwargs["css"] = CUSTOM_CSS
        demo.launch(**share_kwargs)
