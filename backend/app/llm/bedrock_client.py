"""Bedrock Anthropic client (Claude Sonnet 4 in ap-southeast-1).

Uses boto3's `bedrock-runtime` invoke_model with the Anthropic Messages API
shape. Forces tool use to enforce structured output (see structured.py).
boto3 is sync; we offload to a thread.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import boto3

from app.config import settings
from app.core.errors import BedrockFailed
from app.core.logging import get_logger
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.structured import ANALYST_ASSIST_TOOL_NAME, analyst_assist_tool

logger = get_logger(__name__)


# boto3 clients are thread-safe and expensive to construct (signs creds,
# parses service models). Cache one per region for the process lifetime so
# every Depends-created BedrockClient shares it.
_CLIENT_CACHE: dict[str, Any] = {}


def _get_runtime_client(region: str) -> Any:
    client = _CLIENT_CACHE.get(region)
    if client is None:
        client = boto3.client("bedrock-runtime", region_name=region)
        _CLIENT_CACHE[region] = client
    return client


class BedrockClient:
    def __init__(self) -> None:
        self._client = _get_runtime_client(settings.bedrock_region)
        self._model_id = settings.bedrock_model_id
        self._max_tokens = settings.bedrock_max_tokens

    async def invoke_structured(self, messages: list[dict]) -> dict[str, Any]:
        """Force Claude to produce a structured analyst-assist payload via tool use."""
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self._max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": messages,
            "tools": [analyst_assist_tool()],
            "tool_choice": {"type": "tool", "name": ANALYST_ASSIST_TOOL_NAME},
        }
        try:
            resp = await asyncio.to_thread(
                self._client.invoke_model,
                modelId=self._model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("bedrock_invoke_failed", error=str(exc), model=self._model_id)
            raise BedrockFailed(f"Bedrock invoke failed: {exc}") from exc

        try:
            payload = json.loads(resp["body"].read())
        except Exception as exc:  # noqa: BLE001
            raise BedrockFailed(f"Bedrock response parse failed: {exc}") from exc

        for block in payload.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == ANALYST_ASSIST_TOOL_NAME:
                return block.get("input") or {}

        # Fallback: model returned text instead of tool call.
        text = next(
            (b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"),
            "",
        )
        raise BedrockFailed(f"Bedrock did not produce structured tool output. Got text: {text[:200]}")
