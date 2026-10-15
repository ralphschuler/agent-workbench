#!/usr/bin/env python3
"""Read-only, typed Jev decisions for agent MCP clients.

Jev ranks the caller's alternatives and assesses supplied evidence. The server
never executes an alternative or manufactures a natural-language answer.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import math
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from typesafe_sdk import AsyncTypeSafeClient, Choice, Image, Noul, RetryPolicy, TypeSafeError


LOGGER = logging.getLogger("agent.jev")
ABSTAIN = "need_more_information"
MAX_ITEMS = 32
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def threshold_value(value: float) -> float:
    if not math.isfinite(value) or not 0.5 < value <= 1:
        raise ValueError("confidence_threshold must be greater than 0.5 and at most 1")
    return value


def validate_text(value: str, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must contain between 1 and {limit} characters")


def load_image(image_path: str | None) -> Image | None:
    """Read an explicitly requested image once, inside configured image roots."""
    if image_path is None:
        return None
    path = Path(image_path).resolve(strict=True)
    roots = [Path(root).resolve() for root in os.getenv("JEV_IMAGE_ROOTS", "/workspace:/tmp").split(os.pathsep) if root]
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError("image_path must be inside a configured JEV_IMAGE_ROOTS directory")
    if not path.is_file():
        raise ValueError("image_path must point to a regular PNG or JPEG file")
    with path.open("rb") as image_file:
        data = image_file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image_path exceeds the 10 MiB image limit")
    if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")):
        raise ValueError("image_path must contain a PNG or JPEG image")
    return Image(data)


def probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Jev returned an invalid probability")
    return float(value)


class JevRuntime:
    """Keep one connection pool per MCP process, with bounded requests."""

    def __init__(self) -> None:
        self.client: AsyncTypeSafeClient | None = None
        self.timeout = float(os.getenv("JEV_TIMEOUT_SECONDS", "8"))
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 60:
            raise ValueError("JEV_TIMEOUT_SECONDS must be greater than zero and at most 60")

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()

    async def request(self, state: dict[str, Any], questions: dict[str, Any]) -> tuple[Any | None, str | None]:
        try:
            if self.client is None:
                if not os.getenv("TYPESAFE_API_KEY", "").strip():
                    return None, "unconfigured"
                self.client = AsyncTypeSafeClient(timeout=self.timeout, retry=RetryPolicy(max_retries=0))
            async with asyncio.timeout(self.timeout):
                response = await self.client.system_one(state=state, questions=questions)
            return response, None
        except TimeoutError:
            return None, "timeout"
        except TypeSafeError as error:
            # Exception messages can contain server payloads. Keep content out
            # of both MCP errors and logs; the agent can continue without Jev.
            LOGGER.warning("Jev request failed (%s)", type(error).__name__)
            return None, "unavailable"

    async def decide(
        self,
        context: str,
        question: str,
        candidates: dict[str, str],
        image_path: str | None = None,
        confidence_threshold: float = 0.85,
    ) -> dict[str, Any]:
        validate_text(context, "context", 64000)
        validate_text(question, "question", 8000)
        threshold = threshold_value(confidence_threshold)
        if not 1 <= len(candidates) <= MAX_ITEMS:
            raise ValueError(f"provide between 1 and {MAX_ITEMS} candidates")
        for label, description in candidates.items():
            validate_text(label, "candidate label", 128)
            validate_text(description, "candidate description", 4000)
        if ABSTAIN in candidates:
            raise ValueError(f"{ABSTAIN} is a reserved candidate label")
        criteria = dict(candidates)
        criteria[ABSTAIN] = "The supplied evidence does not support choosing any alternative reliably."
        state: dict[str, Any] = {"context": context, "question": question, "alternatives": candidates}
        image = load_image(image_path)
        if image is not None:
            state["image"] = image
        response, error = await self.request(
            state,
            {
                "decision": Choice(
                    instructions="Answer the question using only the supplied evidence. Select need_more_information when evidence is missing or ambiguous.",
                    criteria=criteria,
                ),
                "enough_evidence": Noul(
                    instructions="Does the supplied state contain sufficient reliable evidence to answer the question with one of the substantive alternatives, without guessing?",
                ),
            },
        )
        if error:
            return {"status": error, "abstain": True, "selected": None}
        try:
            answer = response.choices["decision"]
            sufficient = probability(response.nouls["enough_evidence"].noul)
            confidence = probability(answer.confidence)
            probabilities = {label: probability(value) for label, value in answer.probabilities.items()}
            if set(probabilities) != set(criteria) or answer.choice not in criteria:
                raise ValueError("Unexpected alternative")
            if abs(sum(probabilities.values()) - 1) > 0.05:
                raise ValueError("Invalid probability distribution")
            if probabilities[answer.choice] < max(probabilities.values()):
                raise ValueError("Selected alternative is not the most likely")
        except (AttributeError, KeyError, TypeError, ValueError):
            return {"status": "invalid_response", "abstain": True, "selected": None}
        accepted = answer.choice != ABSTAIN and min(confidence, sufficient, probabilities[answer.choice]) >= threshold
        return {
            "status": "ok",
            "abstain": not accepted,
            "selected": answer.choice if accepted else None,
            "proposed": answer.choice,
            "confidence": confidence,
            "probability_enough_evidence": sufficient,
            "probabilities": probabilities,
            "threshold": threshold,
            "model": response.model,
            "usage": response.usage.model_dump(),
        }

    async def assess(
        self,
        context: str,
        questions: dict[str, str],
        image_path: str | None = None,
        confidence_threshold: float = 0.85,
    ) -> dict[str, Any]:
        validate_text(context, "context", 64000)
        threshold = threshold_value(confidence_threshold)
        if not 1 <= len(questions) <= MAX_ITEMS:
            raise ValueError(f"provide between 1 and {MAX_ITEMS} questions")
        for name, question in questions.items():
            validate_text(name, "question name", 128)
            validate_text(question, "question", 8000)
        state: dict[str, Any] = {"context": context}
        image = load_image(image_path)
        if image is not None:
            state["image"] = image
        response, error = await self.request(state, {name: Noul(instructions=question) for name, question in questions.items()})
        if error:
            return {"status": error, "abstain": True, "answers": {}}
        try:
            probabilities = {name: probability(response.nouls[name].noul) for name in questions}
        except (AttributeError, KeyError, TypeError, ValueError):
            return {"status": "invalid_response", "abstain": True, "answers": {}}
        answers = {
            name: {"probability_yes": value, "outcome": "yes" if value >= threshold else "no" if value <= 1 - threshold else "uncertain"}
            for name, value in probabilities.items()
        }
        return {
            "status": "ok",
            "abstain": any(answer["outcome"] == "uncertain" for answer in answers.values()),
            "answers": answers,
            "threshold": threshold,
            "model": response.model,
            "usage": response.usage.model_dump(),
        }


@asynccontextmanager
async def lifespan(_server: FastMCP):
    runtime = JevRuntime()
    try:
        yield runtime
    finally:
        await runtime.close()


mcp = FastMCP(
    "jev",
    instructions="Use Jev for bounded typed decisions and batched yes/no questions about supplied evidence. Preserve abstention. Jev does not generate explanations, verify external facts, or authorize actions.",
    lifespan=lifespan,
)


@mcp.tool()
async def jev_decide(
    context: str,
    question: str,
    candidates: dict[str, str],
    ctx: Context,
    image_path: str | None = None,
    confidence_threshold: float = 0.85,
) -> dict[str, Any]:
    """Rank explicit alternatives using current evidence, retaining probabilities.

    An optional PNG/JPEG is uploaded to TypeSafe along with context. Pass only
    evidence appropriate for that service. Returns selected=null on uncertain,
    unconfigured, failed, or timed-out decisions. Never executes the selection.
    """
    runtime: JevRuntime = ctx.request_context.lifespan_context
    return await runtime.decide(context, question, candidates, image_path, confidence_threshold)


@mcp.tool()
async def jev_assess(
    context: str,
    questions: dict[str, str],
    ctx: Context,
    image_path: str | None = None,
    confidence_threshold: float = 0.85,
) -> dict[str, Any]:
    """Answer up to 32 independent yes/no questions in one TypeSafe request.

    Returns yes/no/uncertain plus probability_yes for each named question.
    An optional PNG/JPEG is uploaded to TypeSafe with context. The caller must
    check outcomes against real application state before reporting completion.
    """
    runtime: JevRuntime = ctx.request_context.lifespan_context
    return await runtime.assess(context, questions, image_path, confidence_threshold)


if __name__ == "__main__":
    mcp.run(transport="stdio")
