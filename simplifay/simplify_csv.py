#!/usr/bin/env python3
"""
simplify_csv.py

Simplify Uzbek text in a large CSV file using a locally running Ollama model
(qwen3:4b). Designed for maximum reliability:

- Resumes from the last processed row after a restart / crash.
- Saves the CSV to disk after EVERY processed row (no batching, no data loss).
- Retries each failed request up to 3 times before giving up on a row.
- Shows a tqdm progress bar with live processing speed and ETA.
- Handles Ctrl+C gracefully (finishes the current save, then exits cleanly).
- Checks that Ollama is actually running before starting any work.
- Works on Windows, macOS and Linux. UTF-8 everywhere.

--------------------------------------------------------------------------
Install requirements (Python 3.12):

    pip install pandas tqdm requests ollama

(The script will use the official `ollama` Python library if it is
installed; otherwise it automatically falls back to the raw HTTP API,
so `pip install requests` alone is enough if you prefer not to install
the `ollama` package.)

Make sure the model is pulled locally before running:

    ollama pull qwen3:4b

And that the Ollama server is running:

    ollama serve

--------------------------------------------------------------------------
Usage:

    python simplify_csv.py --input input.csv --output output.csv

If --output is omitted, the script edits/creates "input.csv" in place
(recommended: always pass --output to keep the original file untouched).
--------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from tqdm import tqdm

# Try to import the official ollama Python library. It's optional -- if it's
# not installed, we transparently fall back to the raw HTTP API below.
try:
    import ollama  # type: ignore
    HAS_OLLAMA_LIB = True
except ImportError:
    HAS_OLLAMA_LIB = False


# ============================================================================
# Configuration
# ============================================================================

MODEL_NAME = "qwen2.5:7b-instruct"  # No "thinking" mode (unlike qwen3), so no
                                    # risk of slow hidden reasoning passes.
                                    # Stronger instruction-following than
                                    # gemma3:4b. ~4.7GB download.
                                    # Override with --model to try another model.
OLLAMA_HOST = "http://localhost:11434"
TEXT_COLUMN = "text"
OUTPUT_COLUMN = "simplified_text"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2  # base delay, grows a bit with each retry
REQUEST_TIMEOUT_SECONDS = 180

# Some source documents are extremely long (legal acts can run to tens or
# hundreds of thousands of characters). The model's context window is
# finite, so long texts must be split into chunks before being sent --
# otherwise the tail of the document is silently truncated and facts get
# lost, which violates the "preserve all facts" rule.
#
# The context window has to hold: system prompt + instructions (input) +
# the chunk of source text (input) + the simplified output (output).
# We split the available budget so the chunk and its expected output each
# get a fair share -- if num_predict is too small relative to chunk size,
# the model's answer gets cut off mid-sentence (this happened in testing).
NUM_CTX = 8192  # tokens of context to request from Ollama (fits comfortably
                # on a 6GB GPU alongside a 3B model; raise if you have more VRAM)
CHARS_PER_TOKEN_ESTIMATE = 3  # conservative estimate for Uzbek (Cyrillic/Latin) text
SYSTEM_AND_OVERHEAD_TOKENS = 350  # system prompt + instructions + formatting overhead
_available_tokens = NUM_CTX - SYSTEM_AND_OVERHEAD_TOKENS

# Target compression: the simplified output should be about 40% of the
# source length (i.e. a ~60% reduction). The input chunk can therefore be
# noticeably larger than the output budget -- give the input the majority
# of the remaining context and size num_predict to match the *shrunk*
# output instead of a 1:1 split.
TARGET_COMPRESSION_RATIO = 0.40  # simplified length / original length
# Solve input_tokens + input_tokens * TARGET_COMPRESSION_RATIO <= available
INPUT_CHUNK_TOKEN_BUDGET = int(_available_tokens / (1 + TARGET_COMPRESSION_RATIO))
NUM_PREDICT = _available_tokens - INPUT_CHUNK_TOKEN_BUDGET
# Small safety margin so the model isn't cut off if it slightly overshoots
# the target ratio on a given chunk.
NUM_PREDICT = int(NUM_PREDICT * 1.25)
MAX_CHUNK_CHARS = INPUT_CHUNK_TOKEN_BUDGET * CHARS_PER_TOKEN_ESTIMATE

SYSTEM_PROMPT = """You are an expert in Uzbek text simplification and summarization.
Rules:
- Preserve the original meaning.
- Preserve all key facts, names, dates, numbers and organizations.
- Do not invent information.
- Do not add headings, titles, sections, or content that is not a direct simplification of the given text.
- Do not use Markdown formatting (no #, *, **, bullet symbols) unless it was already present in the original text.
- Replace difficult words with simpler synonyms.
- Simplify grammar and sentence structure.
- Cut redundancy, repeated boilerplate, filler phrases and minor/secondary details.
- The simplified text MUST be significantly shorter than the original -- target approximately 40% of the original length (about a 60% reduction in length). Do not simply reformat or lightly reword the original; genuinely compress it.
- Never drop a fact, name, date, number or organization just to save space -- cut wording and redundancy, not information.
- If a piece of text is a document fragment (cut off at the start or end), simplify only what is given -- do not try to complete or guess missing parts.
- Return ONLY the simplified text.
- Do not explain your work."""

USER_PROMPT_TEMPLATE = """Simplify and compress the following Uzbek text to about 40% of its original length (~60% shorter), while keeping every fact, name, date and number:
{text}"""


# ============================================================================
# Logging setup
# ============================================================================

def setup_logging() -> logging.Logger:
    """Configure logging to both console and a log file (UTF-8)."""
    logger = logging.getLogger("simplify_csv")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (UTF-8, Windows-safe)
    file_handler = logging.FileHandler("simplify_csv.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logging()


# ============================================================================
# Ollama availability check
# ============================================================================

def check_ollama_running(host: str = OLLAMA_HOST) -> bool:
    """
    Check whether the Ollama server is reachable.

    Returns True if Ollama responds, False otherwise.
    """
    try:
        response = requests.get(f"{host}/api/tags", timeout=5)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def check_model_available(model_name: str, host: str = OLLAMA_HOST) -> bool:
    """
    Check whether the requested model has been pulled locally.
    Returns True if found, False otherwise (non-fatal -- just a warning).
    """
    try:
        response = requests.get(f"{host}/api/tags", timeout=5)
        response.raise_for_status()
        data = response.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        # Ollama may report "qwen3:4b" or "qwen3:4b-<hash>" style tags.
        return any(model_name in m for m in models)
    except requests.exceptions.RequestException:
        return False


def ensure_ollama_ready(model_name: str = MODEL_NAME, host: str = OLLAMA_HOST) -> None:
    """
    Verify Ollama is running and the model is available.
    Exits the script with a clear message if not.
    """
    logger.info("Checking whether Ollama is running...")

    if not check_ollama_running(host):
        print("\nPlease start Ollama (`ollama serve`) and run the script again.\n")
        logger.error("Ollama is not reachable at %s", host)
        sys.exit(1)

    logger.info("Ollama is running.")

    if not check_model_available(model_name, host):
        logger.warning(
            "Model '%s' was not found in `ollama list`. "
            "Make sure you have run: ollama pull %s",
            model_name, model_name,
        )
    else:
        logger.info("Model '%s' is available.", model_name)


# ============================================================================
# Model call (with automatic library / HTTP fallback)
# ============================================================================

def _strip_thinking_tags(text: str) -> str:
    """
    Safety net: some Qwen3 builds emit <think>...</think> reasoning blocks
    even when think=False isn't fully honored by the server/library version.
    Strip them out so they never leak into the final CSV.
    """
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def call_ollama_via_library(text: str) -> str:
    """Call Ollama using the official `ollama` Python library."""
    response = ollama.chat(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=text)},
        ],
        think=False,  # disable "thinking" mode if supported by the model/lib
        options={
            "temperature": 0.1,   # low temperature -- favor faithful, literal
                                   # simplification over creative rewriting,
                                   # which reduces hallucinated content
            "num_predict": NUM_PREDICT,  # sized to the chunk budget, see config
            "num_ctx": NUM_CTX,   # context window big enough for long chunks
        },
    )
    content = response["message"]["content"].strip()
    return _strip_thinking_tags(content)


def call_ollama_via_http(text: str) -> str:
    """Call Ollama using the raw HTTP API (fallback if `ollama` lib is absent)."""
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=text)},
        ],
        "stream": False,
        "think": False,  # ignored by older servers; harmless if unsupported
        "options": {
            "temperature": 0.1,   # low temperature -- favor faithful, literal
                                   # simplification over creative rewriting
            "num_predict": NUM_PREDICT,  # sized to the chunk budget, see config
            "num_ctx": NUM_CTX,   # context window big enough for long chunks
        },
    }
    response = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    content = data["message"]["content"].strip()
    return _strip_thinking_tags(content)


# Repeated website UI labels from lex.uz that are not part of the actual
# document content (buttons like "suggest an edit", "listen to audio",
# "get a link to this element"). These appear thousands of times across
# the dataset and just add noise / confuse the model -- strip them before
# sending text to the model. Matched case-insensitively, line by line.
BOILERPLATE_LINES = {
    "hujjatga taklif yuborish",
    "audioni tinglash",
    "hujjat elementidan havola olish",
}


def clean_text_for_model(text: str) -> str:
    """
    Remove lex.uz website UI boilerplate (repeated button/link labels) and
    stray invisible formatting characters (e.g. U+200E left-to-right mark)
    from the text before it is sent to the model.

    This is applied only to the copy of the text sent to the model -- the
    original 'text' column in the CSV is left untouched.
    """
    # Strip invisible left-to-right marks that show up throughout the data.
    text = text.replace("\u200e", "")

    cleaned_lines = []
    for line in text.split("\n"):
        if line.strip().lower() in BOILERPLATE_LINES:
            continue
        cleaned_lines.append(line)

    result = "\n".join(cleaned_lines)
    # Collapse the blank-line gaps left behind by removed boilerplate.
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def split_into_chunks(text: str, max_chars: int) -> list[str]:
    """
    Split a long text into chunks no larger than max_chars, breaking on
    paragraph boundaries where possible (falls back to sentence/hard splits
    for a single paragraph that is itself longer than max_chars).

    This keeps each chunk small enough to fit in the model's context window
    while trying not to cut sentences in half.
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        candidate = f"{current}\n{para}" if current else para

        if len(candidate) <= max_chars:
            current = candidate
            continue

        # Current chunk is full -- flush it before handling this paragraph.
        if current:
            chunks.append(current)
            current = ""

        if len(para) <= max_chars:
            current = para
            continue

        # A single paragraph is itself too long -- hard-split it on
        # sentence boundaries (period + space) as a best effort.
        start = 0
        while start < len(para):
            end = start + max_chars
            if end < len(para):
                # try to break at the last sentence boundary within range
                boundary = para.rfind(". ", start, end)
                if boundary != -1 and boundary > start:
                    end = boundary + 1
            chunks.append(para[start:end].strip())
            start = end

    if current:
        chunks.append(current)

    return [c for c in chunks if c.strip()]


def simplify_chunk(chunk: str) -> Optional[str]:
    """
    Send a single chunk of text to the model for simplification, with
    retries. Returns the simplified chunk, or None if all retries failed.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if HAS_OLLAMA_LIB:
                result = call_ollama_via_library(chunk)
            else:
                result = call_ollama_via_http(chunk)

            if result:
                ratio = len(result) / max(len(chunk), 1)
                if ratio > 0.6:
                    logger.warning(
                        "Weak compression: simplified chunk is %.0f%% of "
                        "original length (target ~40%%).", ratio * 100,
                    )
                return result
            raise ValueError("Model returned an empty response.")

        except Exception as exc:  # noqa: BLE001 - catch everything and retry
            last_error = exc
            logger.warning(
                "Attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS * attempt)  # simple backoff

    logger.error("All %d attempts failed. Last error: %s", MAX_RETRIES, last_error)
    return None


def simplify_text(text: str) -> Optional[str]:
    """
    Simplify a full row's text, transparently chunking it first if it's
    too long to fit in the model's context window in one go. Chunks are
    simplified independently and the results are joined back together.

    Returns the simplified text, or None if any chunk failed after retries
    (so the whole row can be safely retried on the next run).
    """
    if not isinstance(text, str) or not text.strip():
        return ""  # nothing to simplify

    text = clean_text_for_model(text)
    if not text:
        return ""  # text was pure boilerplate with nothing left after cleaning

    chunks = split_into_chunks(text, MAX_CHUNK_CHARS)

    if len(chunks) > 1:
        logger.info("Text is long (%d chars) -- split into %d chunks.",
                     len(text), len(chunks))

    simplified_chunks: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        result = simplify_chunk(chunk)
        if result is None:
            logger.error("Chunk %d/%d failed -- row will be retried later.",
                         i, len(chunks))
            return None  # bail out; row stays unprocessed for next run
        simplified_chunks.append(result)

    return "\n\n".join(simplified_chunks)


# ============================================================================
# CSV handling
# ============================================================================

def load_dataframe(input_path: Path, output_path: Path) -> pd.DataFrame:
    """
    Load the dataframe to work on.

    Resume support: if an output file already exists (from a previous run),
    load it instead of the raw input, so already-simplified rows are kept.
    """
    if output_path.exists():
        logger.info("Found existing output file '%s'. Resuming from it.", output_path)
        df = pd.read_csv(output_path, encoding="utf-8")
    else:
        logger.info("Loading input file '%s'.", input_path)
        df = pd.read_csv(input_path, encoding="utf-8")

    if TEXT_COLUMN not in df.columns:
        logger.error("Input CSV must contain a '%s' column.", TEXT_COLUMN)
        sys.exit(1)

    if OUTPUT_COLUMN not in df.columns:
        df[OUTPUT_COLUMN] = pd.NA

    return df


def save_dataframe(df: pd.DataFrame, output_path: Path) -> None:
    """
    Save the dataframe to disk, UTF-8 encoded. Called after every row.

    Writes to a temporary file first, then atomically replaces the target
    file. This avoids leaving a half-written/corrupt CSV if the process is
    killed mid-write, and plays nicer with antivirus / cloud-sync tools
    (OneDrive, Google Drive) that can transiently lock the file.

    If the target file is locked by another program (e.g. it's open in
    Excel) or briefly locked by antivirus/cloud sync, retries a few times
    with a short delay instead of crashing the whole run.
    """
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            df.to_csv(tmp_path, index=False, encoding="utf-8")
            tmp_path.replace(output_path)  # atomic on Windows and POSIX
            return
        except PermissionError as exc:
            if attempt == max_attempts:
                logger.error(
                    "Could not write '%s' after %d attempts (file may be open "
                    "in another program, e.g. Excel). Close it and re-run the "
                    "script to resume. Error: %s",
                    output_path, max_attempts, exc,
                )
                raise
            logger.warning(
                "'%s' is locked (attempt %d/%d). Is it open in Excel or "
                "another program? Retrying in 3s...",
                output_path, attempt, max_attempts,
            )
            time.sleep(3)


def row_needs_processing(value) -> bool:
    """
    Decide whether a row still needs to be processed.

    A row is considered "already done" if simplified_text is a non-empty
    string. NaN, None, or empty string means it still needs processing.
    """
    if pd.isna(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


# ============================================================================
# Main processing loop
# ============================================================================

def process_dataframe(df: pd.DataFrame, output_path: Path, workers: int = 1) -> None:
    """
    Iterate over all rows, simplify text, and save after every processed
    row. Shows a tqdm progress bar with live processing speed and ETA.

    If workers > 1, rows are processed concurrently across multiple threads.
    Ollama can handle several simultaneous requests (see OLLAMA_NUM_PARALLEL
    server setting), and since a small model like qwen2.5:3b-instruct only
    uses a fraction of typical GPU VRAM, running several requests at once
    usually gives a solid throughput boost without changing output quality
    (each row is still simplified independently, one full row per request).
    """
    pending_mask = df[OUTPUT_COLUMN].apply(row_needs_processing)
    pending_indices = df.index[pending_mask].tolist()

    total_rows = len(df)
    already_done = total_rows - len(pending_indices)

    logger.info(
        "Total rows: %d | Already simplified: %d | Remaining: %d | Workers: %d",
        total_rows, already_done, len(pending_indices), workers,
    )

    if not pending_indices:
        logger.info("Nothing to do. All rows are already simplified.")
        return

    success_count = 0
    fail_count = 0
    start_time = time.time()

    # Guards concurrent access to the shared dataframe and the CSV file.
    df_lock = threading.Lock()
    stop_event = threading.Event()  # set on Ctrl+C so workers stop picking up new rows

    progress_bar = tqdm(
        total=len(pending_indices),
        desc="Simplifying",
        unit="row",
        dynamic_ncols=True,
    )

    def handle_result(idx, simplified: Optional[str]) -> None:
        """Apply one row's result to the dataframe and persist it to disk."""
        nonlocal success_count, fail_count

        with df_lock:
            if simplified is None:
                fail_count += 1
                df.at[idx, OUTPUT_COLUMN] = ""
                logger.error("Row %s permanently failed this run.", idx)
            else:
                success_count += 1
                df.at[idx, OUTPUT_COLUMN] = simplified

            try:
                save_dataframe(df, output_path)
            except Exception as save_exc:  # noqa: BLE001
                logger.error(
                    "Failed to save progress for row %s: %s. Will retry on "
                    "the next row's save.", idx, save_exc,
                )

            elapsed = time.time() - start_time
            processed_so_far = success_count + fail_count
            rows_per_sec = processed_so_far / elapsed if elapsed > 0 else 0.0
            remaining = len(pending_indices) - processed_so_far
            eta_seconds = remaining / rows_per_sec if rows_per_sec > 0 else 0

            progress_bar.set_postfix({
                "ok": success_count,
                "fail": fail_count,
                "rows/s": f"{rows_per_sec:.2f}",
                "eta": format_seconds(eta_seconds),
            })
            progress_bar.update(1)

    def worker(idx):
        if stop_event.is_set():
            return idx, None, True  # skip -- shutting down
        original_text = df.at[idx, TEXT_COLUMN]
        result = simplify_text(original_text)
        return idx, result, False

    try:
        if workers <= 1:
            # Simple sequential path (identical behavior to before).
            for idx in pending_indices:
                if stop_event.is_set():
                    break
                _, result, skipped = worker(idx)
                if not skipped:
                    handle_result(idx, result)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(worker, idx): idx for idx in pending_indices}
                for future in as_completed(futures):
                    idx, result, skipped = future.result()
                    if not skipped:
                        handle_result(idx, result)

    except KeyboardInterrupt:
        stop_event.set()
        logger.warning("Interrupted by user (Ctrl+C). Waiting for in-flight "
                        "requests to finish and saving progress...")
        with df_lock:
            save_dataframe(df, output_path)
        logger.info("Progress saved to '%s'. You can safely resume later.", output_path)
        sys.exit(0)

    progress_bar.close()

    logger.info(
        "Finished this run. Success: %d | Failed: %d | Total elapsed: %s",
        success_count, fail_count, format_seconds(time.time() - start_time),
    )

    if fail_count:
        logger.warning(
            "%d row(s) failed and were left blank. Re-run the script to retry them.",
            fail_count,
        )


def format_seconds(seconds: float) -> str:
    """Format a duration in seconds as H:MM:SS for readability."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


# ============================================================================
# Entry point
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simplify Uzbek text in a CSV file using a local Ollama model."
    )
    parser.add_argument(
        "--input", type=str, default="input.csv",
        help="Path to the input CSV file (must contain a 'text' column).",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Path to the output CSV file. Defaults to the input file "
             "(in-place editing) if not provided.",
    )
    parser.add_argument(
        "--model", type=str, default=MODEL_NAME,
        help=f"Ollama model name to use (default: {MODEL_NAME}).",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Number of rows to process concurrently (default: 1, sequential). "
             "Try 2-4 to speed things up -- Ollama can serve several requests "
             "at once, especially with a small model that doesn't fill the GPU. "
             "Also set the OLLAMA_NUM_PARALLEL environment variable to at least "
             "this value before starting Ollama for best results.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    global MODEL_NAME  # allow --model override
    MODEL_NAME = args.model

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path

    if not input_path.exists() and not output_path.exists():
        logger.error("Input file '%s' does not exist.", input_path)
        sys.exit(1)

    # Step 1: make sure Ollama is running and the model is present.
    ensure_ollama_ready(MODEL_NAME, OLLAMA_HOST)

    # Step 2: load data (resumes from output file if it already exists).
    df = load_dataframe(input_path, output_path)

    # Step 3: process rows one at a time (or concurrently), saving after
    # every row.
    process_dataframe(df, output_path, workers=args.workers)

    logger.info("Done. Output written to '%s'.", output_path)


if __name__ == "__main__":
    main()
