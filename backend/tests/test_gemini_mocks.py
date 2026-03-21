import re

import httpx
import pytest
import respx


@pytest.fixture(autouse=True)
def reset_extractor_client(monkeypatch):
    import extractor

    monkeypatch.setenv("GOOGLE_API_KEY", "test-api-key")
    # Ensure only Gemini provider is active in tests
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(extractor, "_client", None)
    yield
    monkeypatch.setattr(extractor, "_client", None)


def _gemini_route():
    return re.compile(
        r"https://generativelanguage\.googleapis\.com/v1beta/models/.+:generateContent.*"
    )


def _gemini_payload(text: str) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": text}],
                }
            }
        ]
    }


@pytest.mark.asyncio
async def test_extraction_success():
    from extractor import recommend_form_types

    with respx.mock(assert_all_called=True) as router:
        router.post(_gemini_route()).mock(
            return_value=httpx.Response(
                200,
                json=_gemini_payload(
                    '[{"form_type":"ACAT","rationale":"Whole-shift acute take reflection"}]'
                ),
            )
        )

        recommendations = await recommend_form_types(
            "I ran a busy ED shift, reviewed multiple patients, and reflected on team flow."
        )

    assert [rec.form_type for rec in recommendations] == ["ACAT"]
    assert recommendations[0].uuid
    assert recommendations[0].rationale == "Whole-shift acute take reflection"


@pytest.mark.asyncio
async def test_extraction_timeout():
    from extractor import recommend_form_types

    with respx.mock(assert_all_called=True) as router:
        router.post(_gemini_route()).mock(side_effect=httpx.ConnectTimeout("timed out"))

        with pytest.raises(httpx.ConnectTimeout):
            await recommend_form_types("Chest pain case with evolving ECG changes.")


@pytest.mark.asyncio
async def test_extraction_malformed_response():
    from extractor import recommend_form_types

    with respx.mock(assert_all_called=True) as router:
        router.post(_gemini_route()).mock(
            return_value=httpx.Response(
                200,
                json=_gemini_payload('{"form_type": "CBD"'),
            )
        )

        recommendations = await recommend_form_types(
            "I assessed a septic patient and reflected on earlier escalation."
        )

    assert len(recommendations) == 0
