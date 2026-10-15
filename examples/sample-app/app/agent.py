"""Support agent: plans with Claude, drafts replies with GPT."""

import anthropic
from openai import OpenAI

PLANNER_MODEL = "claude-opus-5-5"
DRAFTER_MODEL = "gpt-5-mini"

claude = anthropic.Anthropic()
openai = OpenAI()


def plan(ticket: str) -> str:
    msg = claude.messages.create(
        model=PLANNER_MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": f"Plan a reply to:\n{ticket}"}],
    )
    return msg.content[0].text


def draft(plan_text: str) -> str:
    resp = openai.responses.create(model=DRAFTER_MODEL, input=plan_text)
    return resp.output_text
