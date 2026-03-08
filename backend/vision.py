"""
Image-to-text extraction using Gemini Vision.
Extracts clinical case descriptions from photos/screenshots.
"""
import base64
import os
from google import genai
from google.genai import types

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    return _client


async def extract_from_image(image_path: str) -> str:
    """Extract case description from clinical photo/screenshot using Gemini Vision."""
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

    response = client.models.generate_content(
        model="gemini-2.0-flash-001",
        contents=contents,
    )

    return response.text.strip()
