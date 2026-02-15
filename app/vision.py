import base64
import logging

from .groq_client import get_client
from .prompts import VISION

logger = logging.getLogger(__name__)

VISION_MODEL = "llama-3.2-90b-vision-preview"


def analyze_photo(image_bytes: bytes, context: str | None = None) -> str | None:
    client = get_client()
    if not client:
        return None

    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    data_uri = f"data:image/jpeg;base64,{b64_image}"

    user_content = [
        {"type": "image_url", "image_url": {"url": data_uri}},
    ]

    text_prompt = "Analyze this image."
    if context:
        text_prompt = f"User context: {context}\n\nAnalyze this image considering the context above."

    user_content.append({"type": "text", "text": text_prompt})

    try:
        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": VISION},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error("Vision API error: %s", e)
        return None


def analyze_photo_with_voice(image_bytes: bytes, voice_text: str) -> str | None:
    return analyze_photo(image_bytes, context=voice_text)
