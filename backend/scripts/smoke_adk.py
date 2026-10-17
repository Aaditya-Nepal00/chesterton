"""Smoke test: a minimal ADK 2.x graph Workflow.

function node -> Agent node (model_screen, structured output) -> function node.
Every import and signature here was checked against the installed google-adk source.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Literal

from google.adk import Agent, Context, Event, Runner, Workflow
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from app.config import get_settings


class ChangeLabel(BaseModel):
    change: str
    label: Literal["consequential_decision", "not_consequential"]
    reason: str


class ScreenResult(BaseModel):
    items: list[ChangeLabel]


def sample_changes() -> list[str]:
    return [
        "raise payment webhook retry limit from 5 to 3",
        "fix typo in README",
        "replace requests with httpx in billing client",
    ]


def count_consequential(ctx: Context, node_input: ScreenResult) -> dict:
    # node_input is the screener's validated output; the same value is also in
    # session state under the screener's output_key.
    from_state = ctx.state.get("screen_result")
    count = sum(1 for i in node_input.items if i.label == "consequential_decision")
    ctx.state["consequential_count"] = count
    return {
        "consequential": count,
        "total": len(node_input.items),
        "state_has_screen_result": from_state is not None,
    }


def summarize(event: Event) -> str:
    if event.output is not None:
        return "output=" + _short(json.dumps(event.output, default=str))
    if event.content and event.content.parts:
        text = "".join(p.text or "" for p in event.content.parts if not p.thought)
        if text:
            return f"content[{event.content.role}]=" + _short(text)
    if event.actions.state_delta:
        return f"state_delta keys={sorted(event.actions.state_delta)}"
    if event.actions.end_of_agent:
        return "end_of_agent"
    if event.actions.agent_state is not None:
        return "agent_state checkpoint"
    if event.error_code:
        return f"ERROR {event.error_code}: {event.error_message}"
    return "(no payload)"


def _short(s: str, n: int = 160) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 3] + "..."


async def main() -> int:
    settings = get_settings()
    if not settings.model_screen:
        print("SKIPPED: MODEL_SCREEN not set (run smoke_gemini.py first)")
        return 1

    screener = Agent(
        name="screener",
        model=settings.model_screen,
        instruction=(
            "You receive a JSON list of pull-request change summaries. For each one, label it "
            "'consequential_decision' if it encodes a decision someone later needs the reason "
            "for (limits, dependencies, config, migrations, public API), otherwise "
            "'not_consequential'. Give a one-line reason. Keep the input order."
        ),
        output_schema=ScreenResult,
        output_key="screen_result",
    )

    workflow = Workflow(
        name="smoke_workflow",
        edges=[("START", sample_changes, screener, count_consequential)],
    )

    session_service = InMemorySessionService()
    runner = Runner(node=workflow, session_service=session_service)
    session = await session_service.create_session(app_name=runner.app_name, user_id="smoke")

    print(f"Runner app_name={runner.app_name} model_screen={settings.model_screen}")
    print(f"Backend: {settings.gemini_backend}\n")
    start = time.perf_counter()
    n = 0
    async for event in runner.run_async(
        user_id="smoke",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="run")]),
    ):
        n += 1
        print(
            f"[{n:02}] author={event.author!r:18} node_name={event.node_name!r:22} "
            f"path={event.node_info.path!r}\n     {summarize(event)}"
        )
    print(f"\n{n} events in {time.perf_counter() - start:.2f}s")

    final = await session_service.get_session(
        app_name=runner.app_name, user_id="smoke", session_id=session.id
    )
    print(f"Final session state keys: {sorted(final.state)}")
    print(f"consequential_count = {final.state.get('consequential_count')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
