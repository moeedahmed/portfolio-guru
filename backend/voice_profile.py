"""
Voice Profile Generator — analyses user-provided portfolio examples
to create a personalised writing style profile.

The profile is a JSON string stored in the user's profile DB.
It's injected into extraction prompts as style guidance.
"""

import json
import logging
from typing import List

from extractor import _generate

logger = logging.getLogger(__name__)


async def generate_voice_profile(examples: List[str]) -> str:
    """Analyse 3-5 portfolio entry examples and generate a writing style profile.

    Args:
        examples: List of text strings from user's previous portfolio entries

    Returns:
        JSON string containing the style profile
    """
    combined = "\n\n---EXAMPLE---\n\n".join(examples)

    prompt = f"""You are a writing style analyser for medical portfolio entries (WPBA forms for UK EM trainees).

Analyse these {len(examples)} examples of portfolio entries written by the same doctor. Extract their personal writing style patterns.

EXAMPLES:
{combined}

Return ONLY a JSON object with these fields:
{{
    "voice_summary": "One paragraph describing this doctor's writing voice",
    "sentence_style": "short/medium/long — typical sentence length preference",
    "person": "first/third — which person they write in",
    "formality": "casual/professional/formal — tone level",
    "reflection_depth": "shallow/moderate/deep — how much they reflect vs describe",
    "favourite_phrases": ["list of 3-5 phrases or sentence starters they reuse"],
    "structure_pattern": "how they typically structure entries (e.g. 'situation then action then reflection')",
    "avoids": ["patterns or words they clearly avoid"],
    "clinical_detail_level": "brief/moderate/thorough — how much clinical detail they include",
    "sample_opening": "A typical opening sentence in their style",
    "sample_reflection": "A typical reflection sentence in their style"
}}

Rules:
- Analyse the actual text — don't invent patterns not present in the examples
- If examples are too few or varied to identify a clear pattern, say so in voice_summary
- British English spelling throughout
- Return ONLY the JSON. No explanation."""

    text = await _generate(prompt)
    raw = text.strip()

    # Strip markdown code fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    # Validate it's valid JSON
    profile = json.loads(raw)

    # Ensure required fields exist
    required = ["voice_summary", "sentence_style", "person", "formality"]
    for field in required:
        if field not in profile:
            profile[field] = "not determined"

    logger.info(f"Voice profile generated from {len(examples)} examples: {profile.get('voice_summary', '')[:80]}...")
    return json.dumps(profile)


def build_voice_instruction(profile_json: str) -> str:
    """Convert a stored voice profile JSON into prompt instructions for extraction.

    Returns a string block that can be injected into extraction prompts.
    """
    try:
        profile = json.loads(profile_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    summary = profile.get("voice_summary", "")
    person = profile.get("person", "first")
    formality = profile.get("formality", "professional")
    sentence_style = profile.get("sentence_style", "medium")
    phrases = profile.get("favourite_phrases", [])
    structure = profile.get("structure_pattern", "")
    avoids = profile.get("avoids", [])
    sample_opening = profile.get("sample_opening", "")
    sample_reflection = profile.get("sample_reflection", "")
    detail = profile.get("clinical_detail_level", "moderate")

    parts = [
        "\n===== PERSONAL WRITING STYLE =====",
        f"Match this doctor's personal writing voice: {summary}",
        f"- Write in {person} person, {formality} tone, {sentence_style} sentences",
        f"- Clinical detail level: {detail}",
    ]
    if structure:
        parts.append(f"- Structure: {structure}")
    if phrases:
        parts.append(f"- Use phrases like: {', '.join(phrases[:5])}")
    if avoids:
        parts.append(f"- Avoid: {', '.join(avoids[:5])}")
    if sample_opening:
        parts.append(f"- Example opening: \"{sample_opening}\"")
    if sample_reflection:
        parts.append(f"- Example reflection: \"{sample_reflection}\"")
    parts.append("Match this style naturally — don't force every pattern into every entry.")

    return "\n".join(parts)
