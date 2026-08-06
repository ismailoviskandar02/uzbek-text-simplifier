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


EXAMPLES = [
    ["Oʻzbekiston Respublikasi Vazirlar Mahkamasining qarori bilan tasdiqlangan Nizomga muvofiq, davlat organlari oʻz vakolatlari doirasida tegishli chora-tadbirlarni amalga oshiradilar."],
]

with gr.Blocks(title="Oʻzbek matnini soddalashtirish") as demo:
    gr.Markdown(
        """
        # 🇺🇿 Oʻzbek matnini soddalashtirish
        Murakkab matnni (huquqiy, rasmiy, davlat hujjati) tushunarli tilga o'giradi.

        _Uzbek Text Simplifier — fine-tuned mT5-small_
        """
    )

    with gr.Row():
        with gr.Column():
            input_box = gr.Textbox(
                label="Asl matn",
                placeholder="Murakkab matnni shu yerga joylashtiring...",
                lines=10,
            )
            beams_slider = gr.Slider(
                minimum=1, maximum=8, value=4, step=1,
                label="Beam search (sifat vs tezlik)",
            )
            submit_btn = gr.Button("Soddalashtirish", variant="primary")

        with gr.Column():
            output_box = gr.Textbox(
                label="Soddalashtirilgan matn",
                lines=10,
                interactive=False,
            )

    gr.Examples(examples=EXAMPLES, inputs=input_box)

    submit_btn.click(fn=simplify, inputs=[input_box, beams_slider], outputs=output_box)
    input_box.submit(fn=simplify, inputs=[input_box, beams_slider], outputs=output_box)

if __name__ == "__main__":
    try:
        demo.launch(server_name="127.0.0.1", server_port=7860)
    except ValueError:
        # На некоторых Windows-конфигурациях localhost блокируется прокси/файрволом —
        # тогда gradio создаёт временную публичную ссылку (*.gradio.live).
        print("Локальный доступ недоступен, создаю публичную ссылку...")
        demo.launch(share=True)
