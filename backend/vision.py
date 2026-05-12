"""
Image-to-text extraction using Gemini Vision.
Extracts clinical case descriptions from photos/screenshots.
"""
import base64
import os
from google import genai
from google.genai import types
from model_config import gemini_fallback_models, openai_fallback_model

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    return _client


async def extract_from_image(image_path: str) -> str:
    """Extract case description from clinical photo/screenshot using Gemini Vision."""
    import asyncio
    client = _get_client()

    # Read and encode image
    with open(image_path, "rb") as f:
        image_data = f.read()

    # Determine mime type
    ext = os.path.splitext(image_path)[1].lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    mime_type = mime_types.get(ext, "image/jpeg")

    prompt = """Extract the clinical case description from this image.

If this is a photo of a non-clinical subject (selfie, food, scenery, pet, random object, meme, etc.),
respond with EXACTLY the word: NOT_CLINICAL

If this is a screenshot of text (e.g. from notes, a message, or a document), transcribe all the clinical text.

If this is a clinical photo, describe what you see in clinical terms that could be used for a case discussion.

Return ONLY the extracted/described text, no additional commentary."""

    # Build content with image
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=image_data, mime_type=mime_type),
                types.Part.from_text(text=prompt),
            ]
        )
    ]

    # Run sync Gemini call in thread pool with model fallback
    loop = asyncio.get_event_loop()
    models_to_try = gemini_fallback_models()
    last_error = None
    for model in models_to_try:
        try:
            response = await loop.run_in_executor(
                None,
                lambda m=model: client.models.generate_content(model=m, contents=contents)
            )
            return response.text.strip()
        except Exception as e:
            error_msg = str(e).lower()
            if any(t in error_msg for t in ["503", "unavailable", "overloaded", "404", "429", "quota", "resource"]):
                last_error = e
                logger.warning(f"Vision model {model} failed: {e} — trying next")
                continue
            raise

    # Gemini quota exhausted — fall back to OpenAI vision (gpt-4o-mini)
    logger.info("Gemini vision quota exhausted — falling back to OpenAI vision")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            import base64 as _b64
            from openai import AsyncOpenAI
            oai = AsyncOpenAI(api_key=openai_key)
            b64_image = _b64.b64encode(image_data).decode("utf-8")
            oai_prompt = prompt
            resp = await oai.chat.completions.create(
                model=openai_fallback_model(),
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": oai_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}},
                    ]
                }],
                max_tokens=1500,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e2:
            logger.error(f"OpenAI vision fallback also failed: {e2}")

    raise last_error
