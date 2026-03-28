"""English to Chinese translation using OpenAI GPT."""
import re
import time
import logging
from openai import OpenAI

log = logging.getLogger("pipeline")

SYSTEM_PROMPT = (
    "你是專業的英中即時口譯員。請將以下英文逐句翻譯成自然流暢的繁體中文。\n"
    "規則：\n"
    "1. 保持口語化、自然的語氣，適合配音使用\n"
    "2. 每行一句翻譯，保持與原文相同的編號\n"
    "3. 只輸出翻譯結果，不要加任何解釋\n"
    "4. 專有名詞可保留英文或使用常見中文譯名\n"
    "5. 翻譯長度盡量簡潔，不要比原文長太多"
)


def translate_segments(
    segments: list[dict],
    client: OpenAI,
    model: str = "gpt-4o",
    batch_size: int = 15,
    on_progress=None,
) -> list[dict]:
    """Translate all segments from English to Chinese.

    Args:
        segments: List of transcribed segments
        client: OpenAI client
        model: GPT model to use
        batch_size: Segments per API call
        on_progress: Callback(batch_index, total_batches)

    Returns:
        Segments with added "translated" field
    """
    translated = []
    total_batches = (len(segments) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(segments), batch_size):
        batch = segments[batch_idx : batch_idx + batch_size]

        # Build context from previous translations
        context = ""
        if translated:
            recent = translated[-3:]
            context = "前文翻譯參考：\n"
            context += "\n".join(f"- {s['translated']}" for s in recent)
            context += "\n\n"

        # Create numbered source text
        numbered = "\n".join(
            f"{j + 1}. {s['text']}" for j, s in enumerate(batch)
        )

        user_content = f"{context}請翻譯：\n{numbered}"
        # Remove control characters that break JSON serialization
        user_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', user_content)

        # 最多重試 3 次，使用指數退避（2^attempt 秒）
        response = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 第一次嘗試帶 context，後續重試不帶 context
                if attempt == 0:
                    msg_content = user_content
                else:
                    msg_content = f"請翻譯：\n{numbered}"
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": msg_content},
                    ],
                    temperature=0.3,
                )
                break  # 成功則跳出重試迴圈
            except Exception as e:
                log.warning(f"[Translate] batch attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    wait = 2 ** attempt  # 指數退避：1s, 2s
                    time.sleep(wait)

        if response is not None:
            result_text = response.choices[0].message.content.strip()
            lines = [l.strip() for l in result_text.split("\n") if l.strip()]
        else:
            # 全部重試失敗，使用原文作為後備（不中斷 pipeline）
            log.warning(f"[Translate] batch all {max_retries} retries exhausted, using original text as fallback")
            lines = []

        for j, seg in enumerate(batch):
            if j < len(lines):
                text = re.sub(r"^\d+[\.\、\)\]\s]+", "", lines[j]).strip()
                translated.append({**seg, "translated": text})
            else:
                # 後備：使用原文
                translated.append({**seg, "translated": seg["text"]})

        if on_progress:
            on_progress(batch_idx // batch_size + 1, total_batches)

    return translated
