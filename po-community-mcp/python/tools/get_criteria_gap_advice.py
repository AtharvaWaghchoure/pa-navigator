"""
Given unmet PA criteria, advises the physician on what clinical documentation to gather.

Returns plain-language, actionable guidance — what to document, what tests to order,
or what clinical records to retrieve — so the physician can strengthen the PA request.
"""
import json
import os
from typing import Annotated

import litellm
from mcp.server.fastmcp import Context
from pydantic import Field

from mcp_utilities import create_text_response

_MODEL = os.getenv("MCP_MODEL", "gemini/gemini-2.5-flash")


async def get_criteria_gap_advice(
    notMetCriteria: Annotated[  # noqa: N803
        str,
        Field(description="JSON array of unmet criteria from MatchCriteria output (the 'not_met' field)"),
    ],
    ctx: Context = None,
) -> str:
    try:
        not_met = json.loads(notMetCriteria)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON input: {e}") from e

    if not not_met:
        return create_text_response(
            "All criteria are met. No gaps to address — the PA letter should be strong as-is."
        )

    gaps_text = "\n".join(
        f"- {item.get('criterion', 'Unknown criterion')}: {item.get('gap', 'No detail')}"
        for item in not_met
    )

    response = await litellm.acompletion(
        model=_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a prior authorization specialist advising a treating physician. "
                    "For each unmet PA criterion, give specific, actionable advice on what documentation "
                    "to gather, what clinical information to add to the record, or what steps to take "
                    "to satisfy the requirement. Be concise and practical. Use bullet points."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"The following PA criteria are not met or have insufficient documentation:\n\n"
                    f"{gaps_text}\n\n"
                    "What should the physician do to address each gap before resubmitting?"
                ),
            },
        ],
    )

    return create_text_response(response.choices[0].message.content.strip())
