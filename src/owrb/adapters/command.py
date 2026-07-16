"""Command adapter: invoke a local executable with the request on stdin.

System settings:

- ``command``: argv list for the executable.

The process receives a JSON object on stdin::

    {"scenario_instance_id": ..., "prompt": ..., "answer_contract": {...}}

and must print a JSON object to stdout::

    {"answer": "...", "citations": [...], "metrics": {...}, "trace": [...]}

A non-zero exit code becomes a failed run; the runner preserves it rather than
retrying (SPEC.md 14.2). Candidate systems that execute local tools should be
sandboxed by the operator (SPEC.md 21); the adapter itself adds no network or
filesystem restrictions.
"""

from __future__ import annotations

import asyncio
import json

from owrb.adapters.base import AdapterError, utc_now
from owrb.adapters.generic_http import _parse_citations, _parse_metrics
from owrb.models import RunRequest, RunResult

_STDERR_LIMIT = 2000


class CommandAdapter:
    async def run(self, request: RunRequest) -> RunResult:
        command = request.system.settings.get("command")
        if not isinstance(command, list) or not command:
            raise AdapterError("command system requires settings.command as an argv list")
        argv = [str(argument) for argument in command]

        body = json.dumps(
            {
                "scenario_instance_id": request.scenario.id,
                "prompt": request.input_text,
                "answer_contract": request.scenario.answer_contract.model_dump(mode="json"),
            }
        )
        started_at = utc_now()
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate(body.encode("utf-8"))
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        completed_at = utc_now()

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[:_STDERR_LIMIT]
            raise AdapterError(f"command exited with code {process.returncode}: {detail}")
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AdapterError(f"command produced invalid JSON: {error}") from error

        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer:
            raise AdapterError("command output did not contain an answer")
        metrics = _parse_metrics(payload.get("metrics"))
        if metrics.latency_ms is None:
            metrics.latency_ms = int((completed_at - started_at).total_seconds() * 1000)
        raw_trace = payload.get("trace")
        trace = [event for event in raw_trace if isinstance(event, dict)] if isinstance(
            raw_trace, list
        ) else []

        return RunResult(
            scenario_instance_id=request.scenario.id,
            system_id=request.system.id,
            trial_id=request.trial_id,
            status="completed",
            started_at=started_at,
            completed_at=completed_at,
            answer=answer,
            citations=_parse_citations(payload.get("citations")),
            metrics=metrics,
            trace=trace,
        )
