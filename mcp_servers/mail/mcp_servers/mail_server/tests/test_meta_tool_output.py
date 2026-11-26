import json

import main
import pytest
from fastmcp import Client


@pytest.mark.parametrize(
    "request_extra",
    [
        {"action": "help"},
        {"action": "list"},
        {"action": "search", "search_subject": "Gorilla"},
        {"action": "send", "to_email": "b@example.com"},
    ],
)
async def test_meta_tool_output_validates(request_extra):
    async with Client(main.mcp) as client:
        sent = await client.call_tool(
            "mail",
            {
                "request": {
                    "action": "send",
                    "to_email": "keeper@example.com",
                    "subject": "Gorilla note",
                    "body": "/animals/x.txt",
                }
            },
            raise_on_error=False,
        )
        assert not sent.is_error, sent.content[0].text
        assert json.loads(sent.content[0].text)["send"]["success"]

        result = await client.call_tool(
            "mail", {"request": request_extra}, raise_on_error=False
        )
        assert not result.is_error, result.content[0].text
        assert json.loads(result.content[0].text)["action"] == request_extra["action"]
