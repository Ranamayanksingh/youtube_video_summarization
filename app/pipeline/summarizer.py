"""
Summarizes transcribed .txt files using Groq (cloud) or Ollama (local).
Outputs .summary.txt files into the data/summaries/ folder.

Groq is used when GROQ_API_KEY is set in .env. Falls back to Ollama otherwise.
"""
import os
import glob
import argparse

from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_MODEL = "llama-3.3-70b-versatile"  # Groq model
DEFAULT_DOWNLOADS_DIR = os.path.join(_PROJECT_ROOT, "data", "downloads")
DEFAULT_SUMMARIES_DIR = os.path.join(_PROJECT_ROOT, "data", "summaries")

PROMPT_TEMPLATE = """\
You are given a transcript of a YouTube video. Write a detailed and thorough summary in English.

Structure your summary exactly as follows (use these exact emoji headers):

📌 Topic
A 2-3 sentence overview of what the video is about.

📋 Summary
Cover all significant points discussed. Group related points under bold sub-headings. Be thorough — include important details, names, events, and facts. Each sub-section should be detailed enough that someone who hasn't watched the video fully understands what was said.

💡 Key Takeaways
• Bullet point 1
• Bullet point 2
• (continue for all major takeaways)

🏁 Conclusion
2-3 sentences on the overall message or outcome of the video.

Important:
- Do not include ads, promotional content, course promotions, or calls to action.
- Do not pad with filler. Every sentence should add value.
- Write in clear, fluent English.

Transcript:
{text}
"""

PROMPT_TEMPLATE_HINDI = """\
आपको एक YouTube वीडियो का ट्रांसक्रिप्ट दिया गया है। हिंदी में एक विस्तृत और संपूर्ण सारांश लिखें।

सारांश को बिल्कुल इस प्रकार संरचित करें (इन्हीं emoji हेडर का उपयोग करें):

📌 विषय
वीडियो किस बारे में है — 2-3 वाक्यों में संक्षिप्त परिचय।

📋 विस्तृत सारांश
वीडियो में चर्चा किए गए सभी महत्वपूर्ण बिंदुओं को कवर करें। संबंधित बिंदुओं को बोल्ड उप-शीर्षकों के अंतर्गत समूहित करें। विस्तृत रहें — महत्वपूर्ण विवरण, नाम, घटनाएं और तथ्य शामिल करें।

💡 मुख्य बातें
• मुख्य बात 1
• मुख्य बात 2
• (सभी महत्वपूर्ण निष्कर्षों के लिए जारी रखें)

🏁 निष्कर्ष
वीडियो के समग्र संदेश या परिणाम पर 2-3 वाक्य।

महत्वपूर्ण:
- विज्ञापन, प्रचार सामग्री या कॉल-टू-एक्शन शामिल न करें।
- केवल मूल्यवान जानकारी लिखें, भराव सामग्री नहीं।
- स्पष्ट और प्रवाहमय हिंदी में लिखें।

ट्रांसक्रिप्ट:
{text}
"""


def _llm_chat(model: str, prompt: str) -> str:
    """Send a prompt to Groq if API key is set, otherwise fall back to Ollama."""
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        from groq import Groq
        client = Groq(api_key=groq_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    else:
        import ollama
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response["message"]["content"].strip()


def summarize_file(
    txt_path: str,
    summaries_dir: str,
    model: str,
    overwrite: bool,
    prompt_template: str | None = None,
    lang: str = "english",
) -> str | None:
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

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    backend = "Groq" if groq_key else "Ollama"
    print(f"[SUMMARIZING] {base_name} (lang={lang}, backend={backend}, model={model})")

    if prompt_template:
        template = prompt_template
    elif lang == "hindi":
        template = PROMPT_TEMPLATE_HINDI
    else:
        template = PROMPT_TEMPLATE

    summary = _llm_chat(model, template.format(text=text))

    os.makedirs(summaries_dir, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)
        f.write("\n")

    print(f"[DONE] Saved: {summary_path}")
    print(f"\n{summary}\n")
    print("-" * 60)
    return summary_path


def summarize_all(downloads_dir: str, summaries_dir: str, model: str, overwrite: bool):
    txt_files = sorted(glob.glob(os.path.join(downloads_dir, "*.txt")))
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
    parser = argparse.ArgumentParser(description="Summarize transcribed text files.")
    parser.add_argument("--dir", default=DEFAULT_DOWNLOADS_DIR, help="Directory with .txt transcripts")
    parser.add_argument("--summaries-dir", default=DEFAULT_SUMMARIES_DIR, help="Directory to save summaries")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--overwrite", action="store_true", help="Re-summarize files that already have a summary")
    args = parser.parse_args()

    summarize_all(args.dir, summaries_dir=args.summaries_dir, model=args.model, overwrite=args.overwrite)
