from __future__ import annotations

import base64

from openai import OpenAI

from northstar_api.config import get_settings

VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"


def _client() -> OpenAI:
    settings = get_settings()

    if not settings.nvidia_api_key:
        raise RuntimeError("NVIDIA API key is not configured")

    return OpenAI(
        base_url=settings.nvidia_base_url,
        api_key=settings.nvidia_api_key.get_secret_value(),
    )


def image_to_base64(image_path: str) -> str:
    """Convert an image file into a base64 string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def extract_vision_text(image_path: str) -> str:
    """
    Extract only text visibly present in the supplied image.

    The model must not answer, summarize, interpret, or invent information.
    """
    image_base64 = image_to_base64(image_path)

    prompt = """
You are a strict website OCR and text extraction system.

Your ONLY task is to extract text that is visibly present
in the provided image.

STRICT RULES:

1. Extract ONLY text that is visibly present in the image.
2. Do NOT answer any question.
3. Do NOT summarize.
4. Do NOT explain anything.
5. Do NOT infer missing information.
6. Do NOT guess unreadable text.
7. Do NOT create information.
8. Preserve numbers exactly.
9. Preserve prices, fees, percentages and dates exactly.
10. Preserve company names exactly.
11. Preserve headings and labels.
12. Preserve table information as text.
13. If text is not visible, do not include it.
14. Return ONLY the extracted text.

IMPORTANT:
This output will be used as a factual source for an AI chatbot.
Therefore, hallucination is strictly prohibited.

Return only the text visible in the image.
"""

    response = _client().chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}",
                        },
                    },
                ],
            }
        ],
        temperature=0,
        max_tokens=4096,
    )

    return (response.choices[0].message.content or "").strip()
