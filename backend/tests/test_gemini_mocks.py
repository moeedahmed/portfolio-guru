import httpx
import pytest
import respx


@pytest.fixture(autouse=True)
def reset_extractor_client(monkeypatch):
    import extractor

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(extractor, "_client", None)
    yield
    monkeypatch.setattr(extractor, "_client", None)


def _deepseek_route():
    return "https://api.deepseek.com/chat/completions"


def _deepseek_payload(text: str) -> dict:
    return {
        "choices": [
            {"message": {"content": text}}
        ],
    }


@pytest.mark.asyncio
async def test_extraction_success():
    from extractor import recommend_form_types

    with respx.mock(assert_all_called=True) as router:
        router.post(_deepseek_route()).mock(
            return_value=httpx.Response(
                200,
                json=_deepseek_payload(
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
        router.post(_deepseek_route()).mock(side_effect=httpx.ConnectTimeout("timed out"))

        with pytest.raises(Exception):
            await recommend_form_types("Chest pain case with evolving ECG changes.")


@pytest.mark.asyncio
async def test_extraction_malformed_response():
    from extractor import recommend_form_types

    with respx.mock(assert_all_called=True) as router:
        router.post(_deepseek_route()).mock(
            return_value=httpx.Response(
                200,
                json=_deepseek_payload('{"form_type": "CBD"'),
            )
        )

        recommendations = await recommend_form_types(
            "I assessed a septic patient and reflected on earlier escalation."
        )

    assert len(recommendations) == 0


@pytest.mark.asyncio
async def test_plain_text_generation_does_not_force_json_response_format():
    import json
    from extractor import compose_filing_recovery_copy

    with respx.mock(assert_all_called=True) as router:
        route = router.post(_deepseek_route()).mock(
            return_value=httpx.Response(
                200,
                json=_deepseek_payload("Kaizen could not be reached, so retry once the browser session is back."),
            )
        )

        result = await compose_filing_recovery_copy("failed", "All connection attempts failed")

    payload = json.loads(route.calls[0].request.content.decode())
    assert "response_format" not in payload
    assert result == "Kaizen could not be reached, so retry once the browser session is back."
