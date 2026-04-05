"""
Summarizes transcribed .txt files in the downloads folder using a local Ollama model.
Outputs .txt summary files into a separate 'summaries/' folder.
"""
import os
import glob
import argparse
import ollama

DEFAULT_MODEL = "llama3"
DEFAULT_DOWNLOADS_DIR = "downloads"
DEFAULT_SUMMARIES_DIR = "summaries"

PROMPT_TEMPLATE = """\
You are given a transcript of a YouTube video. Write a detailed and thorough summary in English.

Structure your summary as follows:

**Topic**
A 2-3 sentence overview of what the video is about.

**Detailed Summary**
Cover all the significant points discussed in the video. Group related points under sub-headings where appropriate. Be thorough — do not skip important details, events, names, or facts mentioned. Each sub-section should have enough detail that a reader who has not watched the video fully understands what was said.

**Key Takeaways**
A bullet list of the most important facts or conclusions from the video.

**Conclusion**
2-3 sentences on the overall message or outcome of the video.

Important:
- Do not include any ads, promotional content, course promotions, or calls to action.
- Do not pad the summary with filler. Every sentence should add value.
- Write in clear, fluent English.

Transcript:
{text}
"""


def summarize_file(txt_path: str, summaries_dir: str, model: str, overwrite: bool) -> str | None:
    base_name = os.path.splitext(os.path.basename(txt_path))[0]
    summary_path = os.path.join(summaries_dir, base_name + ".summary.txt")

    if os.path.exists(summary_path) and not overwrite:
        print(f"[SKIP] Already summarized: {base_name}")
        return None

    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if not text:
        print(f"[SKIP] Empty file: {base_name}")
        return None

    print(f"[SUMMARIZING] {base_name}")

    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(text=text)}],
    )
    summary = response["message"]["content"].strip()

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)
        f.write("\n")

    print(f"[DONE] Saved: {summary_path}")
    print(f"\n{summary}\n")
    print("-" * 60)
    return summary_path


def summarize_all(downloads_dir: str, summaries_dir: str, model: str, overwrite: bool):
    txt_files = sorted(glob.glob(os.path.join(downloads_dir, "*.txt")))
    # exclude any stale .summary.txt files that may exist in downloads/
    txt_files = [f for f in txt_files if not f.endswith(".summary.txt")]

    if not txt_files:
        print(f"No .txt files found in '{downloads_dir}'")
        return

    os.makedirs(summaries_dir, exist_ok=True)

    print(f"Found {len(txt_files)} transcript(s). Model: {model}")
    print(f"Summaries will be saved to: {summaries_dir}\n")
    print("=" * 60)

    for txt_path in txt_files:
        summarize_file(txt_path, summaries_dir=summaries_dir, model=model, overwrite=overwrite)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Summarize transcribed text files using a local Ollama model.")
    parser.add_argument("--dir", default=DEFAULT_DOWNLOADS_DIR, help="Directory with .txt transcripts (default: downloads)")
    parser.add_argument("--summaries-dir", default=DEFAULT_SUMMARIES_DIR, help="Directory to save summaries (default: summaries)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--overwrite", action="store_true", help="Re-summarize files that already have a summary")
    args = parser.parse_args()

    summarize_all(args.dir, summaries_dir=args.summaries_dir, model=args.model, overwrite=args.overwrite)
