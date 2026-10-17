"""Live-trace self-test: a fake-model workflow that writes a real run to Firestore slowly,
so you can watch the dashboard's Run trace page fill in step by step. Zero Gemini calls.

    uv run python scripts/selftest_live_trace.py [--run-id ID] [--delay 1.5]

The run is written with kind="selftest" and repo="chesterton/selftest";
scripts/cleanup_demo_data.py lists such runs for deletion.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from google.adk import Agent, Context, Workflow
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.workflow import JoinNode
from google.genai import types

from app.agents import model_chain as mc
from app.models.analysis import ScreenResult
from app.models.trace import trace_started
from app.workflows.runner import new_run_id, run_workflow

DELAY = 1.5


class FakeFlash(BaseLlm):
    """Answers after a pause, like a model would. Never calls Gemini."""

    async def generate_content_async(self, llm_request, stream=False):
        await asyncio.sleep(DELAY * 1.5)
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text='{"consequential": true, "reasons": ["self-test"]}')],
            )
        )


def step(name: str):
    async def fn(ctx: Context):
        trace_started(ctx)
        await asyncio.sleep(DELAY)
        return {"summary": f"{name}: done (self-test)"}

    fn.__name__ = name
    return fn


def build() -> Workflow:
    model = mc.FallbackGemini.create(["fake-flash"], factory=lambda n: FakeFlash(model=n))
    screen = Agent(name="screen", model=model, instruction="self-test", output_schema=ScreenResult)
    load, signals, evidence = step("load_context"), step("compute_signals"), step("build_evidence")
    verify, persist = step("verify"), step("persist")
    return Workflow(
        name="selftest",
        edges=[
            (
                "START",
                load,
                (signals, evidence),
                JoinNode(name="join_context"),
                screen,
                verify,
                persist,
            ),
        ],
    )


async def main() -> int:
    global DELAY
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--delay", type=float, default=DELAY)
    args = ap.parse_args()
    DELAY = args.delay
    mc.set_quota_registry(mc.QuotaRegistry(None))  # no quota cache reads/writes
    run_id = args.run_id or new_run_id()
    print(f"run_id={run_id}  watch: /runs/{run_id}", flush=True)
    out = await run_workflow(
        kind="selftest",
        workflow=build(),
        repo="chesterton/selftest",
        pr=0,
        state={"run_id": run_id},
        trigger="selftest",
        persist=True,
        on_trace=lambda ev: print(f"  {ev.seq:>2} {ev.status:8} {ev.node}", flush=True),
    )
    print(
        f"status={out.status} streamed={out.stats.get('events_streamed')} "
        f"events={len(out.recorder.events)}"
    )
    return 0 if out.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
