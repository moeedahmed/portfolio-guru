"""
Image-to-text extraction using Gemini Vision.
Extracts clinical case descriptions from photos/screenshots.
"""
import base64
import logging
import os
from google import genai
from google.genai import types
from model_config import gemini_fallback_models, openai_fallback_model

logger = logging.getLogger(__name__)

_client = None


# Source-grounded image extraction prompt. The earlier version invited the
# model to "describe what you see in clinical terms that could be used for a
# case discussion" — which let it pattern-match imaging findings (e.g. rib
# fractures) into invented ATLS/resuscitation narratives. The rules below
# force facts-only output and explicitly forbid extrapolation into typical
# management or case-discussion framing.
IMAGE_EXTRACTION_PROMPT = """You are extracting clinical evidence from an image for a UK medical e-portfolio entry.

Source-grounding rules — non-negotiable:
- Output ONLY what is explicitly visible in this image.
- Do NOT add interpretation, typical management, or expected next steps.
- Do NOT extrapolate from imaging findings into clinical narrative. Seeing rib fractures, ECG changes, or any imaging finding does NOT mean writing about ATLS, resuscitation, defibrillation, ROSC, CPR, trauma calls, coronary angiography, CT head, or any management the image does not directly document.
- Do NOT add "case discussion" framing — do not invent reflection, learning points, or what the doctor did. Facts only.

Decide what the image is and respond accordingly.

If the image is NON-CLINICAL (selfie, food, scenery, pet, random object, meme, screenshot of unrelated UI):
Respond with EXACTLY the word: NOT_CLINICAL

If the image is TEXT CONTENT (notes, message, document, imaging report, screenshot of a clinical record):
Transcribe the visible clinical text verbatim. Do not summarise. Do not interpret. Skip non-clinical text (timestamps, system headers, app chrome) unless directly relevant.

If the image shows IMAGING or a CLINICAL PHOTO (X-ray, CT, ECG, ultrasound, wound, rash, procedure picture):
List ONLY the findings you can see, in neutral clinical terms. Do not infer cause, severity, management, or outcome.
Example output: "Right-sided rib fractures (ribs 4 to 7). No visible pneumothorax. Right chest wall soft tissue haematoma."
NOT example: "Patient with multiple rib fractures requiring ATLS resuscitation and CT trauma series."

If important information cannot be determined from the image, write: "[Not visible in image: <what is missing>]"
Do NOT guess.

Return the extracted facts only — no preamble, no summary, no commentary."""


def _get_client():
    global _client
    if _client is None:
        from gemini_client import make_client
        _client = make_client()
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

    prompt = IMAGE_EXTRACTION_PROMPT

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
