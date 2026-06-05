"""Unit tests for `app.workers.case_lifecycle_worker._Sweeper`."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.case_lifecycle_worker import (
    AI_ASSIST_MAX_PER_TICK,
    SWEEP_INTERVAL_SECONDS,
    _Sweeper,
)


# --- PR-7 baseline tests ---------------------------------------------


@pytest.mark.asyncio
async def test_run_once_delegates_to_repo_sweep_sla() -> None:
    repo = AsyncMock()
    repo.sweep_sla = AsyncMock(
        return_value={"scanned": 4, "updated": 3, "newly_breached": 1}
    )
    sweeper = _Sweeper(repo=repo, interval_seconds=60, auto_run_ai_assist=False)
    out = await sweeper.run_once()
    # PR-8 augments the return shape with an "ai_assist" sub-dict; the
    # original SLA counters remain top-level for back-compat.
    assert out["scanned"] == 4
    assert out["updated"] == 3
    assert out["newly_breached"] == 1
    repo.sweep_sla.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_loop_calls_run_once_until_shutdown() -> None:
    repo = AsyncMock()
    repo.sweep_sla = AsyncMock(
        return_value={"scanned": 0, "updated": 0, "newly_breached": 0}
    )
    sweeper = _Sweeper(repo=repo, interval_seconds=0.05, auto_run_ai_assist=False)
    task = asyncio.create_task(sweeper.run())
    await asyncio.sleep(0.18)
    await sweeper.shutdown()
    await task
    assert repo.sweep_sla.await_count >= 3


@pytest.mark.asyncio
async def test_shutdown_short_circuits_idle_loop() -> None:
    repo = AsyncMock()
    repo.sweep_sla = AsyncMock(
        return_value={"scanned": 0, "updated": 0, "newly_breached": 0}
    )
    sweeper = _Sweeper(repo=repo, interval_seconds=60, auto_run_ai_assist=False)
    task = asyncio.create_task(sweeper.run())
    await asyncio.sleep(0.05)
    await sweeper.shutdown()
    # Must NOT timeout — the long sleep should be cancelled by the stop event.
    await asyncio.wait_for(task, timeout=2.0)


def test_sweep_interval_seconds_module_constant() -> None:
    assert SWEEP_INTERVAL_SECONDS == 60


# --- PR-8 ai_assist auto-run tests -----------------------------------


def _sla_zero() -> dict:
    return {"scanned": 0, "updated": 0, "newly_breached": 0}


@pytest.mark.asyncio
async def test_run_once_skips_ai_assist_when_flag_off() -> None:
    repo = AsyncMock()
    repo.sweep_sla = AsyncMock(return_value=_sla_zero())
    repo.list_open = AsyncMock(return_value=[])
    assist = AsyncMock()
    sweeper = _Sweeper(
        repo=repo,
        ai_assist_service=assist,
        auto_run_ai_assist=False,
    )

    out = await sweeper.run_once()

    repo.sweep_sla.assert_awaited_once()
    assist.generate.assert_not_awaited()
    # When flag is off, ai_assist counters stay at the zero default —
    # they're not even computed (no list_open call against the repo).
    repo.list_open.assert_not_called()
    assert out["ai_assist"] == {
        "considered": 0,
        "generated": 0,
        "skipped": 0,
        "errors": 0,
    }


@pytest.mark.asyncio
async def test_run_once_triggers_ai_assist_when_flag_on() -> None:
    repo = AsyncMock()
    repo.sweep_sla = AsyncMock(return_value=_sla_zero())
    repo.list_open = AsyncMock(
        return_value=[
            {"case_id": "c-1"},  # no ai_assist → fire
            {"case_id": "c-2", "ai_assist": {"summary": "already done"}},
            {"case_id": "c-3", "ai_assist": None},  # falsy → fire
        ]
    )
    assist = AsyncMock()
    assist.generate = AsyncMock(return_value={"cached": False})
    sweeper = _Sweeper(
        repo=repo,
        ai_assist_service=assist,
        auto_run_ai_assist=True,
    )

    out = await sweeper.run_once()

    assert assist.generate.await_count == 2
    called_case_ids = {
        kw["case_id"] for _, kw in [c for c in assist.generate.call_args_list]
    }
    assert called_case_ids == {"c-1", "c-3"}
    assert out["ai_assist"]["considered"] == 3
    assert out["ai_assist"]["generated"] == 2
    assert out["ai_assist"]["skipped"] == 1
    assert out["ai_assist"]["errors"] == 0


@pytest.mark.asyncio
async def test_run_once_per_case_failures_do_not_kill_batch() -> None:
    repo = AsyncMock()
    repo.sweep_sla = AsyncMock(return_value=_sla_zero())
    repo.list_open = AsyncMock(
        return_value=[
            {"case_id": "c-1"},
            {"case_id": "c-2"},
            {"case_id": "c-3"},
        ]
    )
    assist = AsyncMock()

    async def _generate(*, case_id: str) -> dict:
        if case_id == "c-2":
            raise RuntimeError("bedrock blew up")
        return {"cached": False}

    assist.generate = AsyncMock(side_effect=_generate)
    sweeper = _Sweeper(
        repo=repo,
        ai_assist_service=assist,
        auto_run_ai_assist=True,
    )

    out = await sweeper.run_once()

    # All three were attempted — second failure didn't short-circuit.
    assert assist.generate.await_count == 3
    assert out["ai_assist"]["generated"] == 2
    assert out["ai_assist"]["errors"] == 1
    assert out["ai_assist"]["considered"] == 3


@pytest.mark.asyncio
async def test_run_once_bounded_by_max_per_tick() -> None:
    candidates = [{"case_id": f"c-{i}"} for i in range(50)]
    repo = AsyncMock()
    repo.sweep_sla = AsyncMock(return_value=_sla_zero())
    repo.list_open = AsyncMock(return_value=candidates)
    assist = AsyncMock()
    assist.generate = AsyncMock(return_value={"cached": False})
    sweeper = _Sweeper(
        repo=repo,
        ai_assist_service=assist,
        auto_run_ai_assist=True,
        max_ai_assist_per_tick=AI_ASSIST_MAX_PER_TICK,
    )

    out = await sweeper.run_once()

    # At most the per-tick bound is fired; the rest will be picked up
    # on the next sweep.
    assert assist.generate.await_count <= AI_ASSIST_MAX_PER_TICK
    assert out["ai_assist"]["generated"] <= AI_ASSIST_MAX_PER_TICK
    assert assist.generate.await_count == AI_ASSIST_MAX_PER_TICK


@pytest.mark.asyncio
async def test_run_once_explicit_max_per_tick_caps_lower() -> None:
    candidates = [{"case_id": f"c-{i}"} for i in range(10)]
    repo = AsyncMock()
    repo.sweep_sla = AsyncMock(return_value=_sla_zero())
    repo.list_open = AsyncMock(return_value=candidates)
    assist = AsyncMock()
    assist.generate = AsyncMock(return_value={"cached": False})
    sweeper = _Sweeper(
        repo=repo,
        ai_assist_service=assist,
        auto_run_ai_assist=True,
        max_ai_assist_per_tick=3,
    )
    out = await sweeper.run_once()
    assert assist.generate.await_count == 3
    assert out["ai_assist"]["generated"] == 3


@pytest.mark.asyncio
async def test_main_skips_building_ai_service_when_flag_off() -> None:
    """When AI_ASSIST_AUTO_RUN is off, `main()` must NOT instantiate the
    heavyweight AiAssistService stack.
    """
    # We patch every external dep so we never hit Mongo, Bedrock, or
    # the signal handler machinery.
    fake_db = MagicMock(name="fake_db")

    # Stub the long-running .run() so main() exits promptly.
    async def _noop_run(self) -> None:  # noqa: ARG001
        return None

    with (
        patch(
            "app.workers.case_lifecycle_worker.connect_mongo",
            new=AsyncMock(),
        ),
        patch(
            "app.workers.case_lifecycle_worker.disconnect_mongo",
            new=AsyncMock(),
        ),
        patch(
            "app.workers.case_lifecycle_worker.get_db",
            return_value=fake_db,
        ),
        patch(
            "app.workers.case_lifecycle_worker.QuarantineCaseRepository"
        ) as repo_cls,
        patch(
            "app.workers.case_lifecycle_worker._build_ai_assist_service"
        ) as build_assist,
        patch(
            "app.workers.case_lifecycle_worker.configure_logging"
        ),
        patch(
            "app.workers.case_lifecycle_worker._Sweeper.run",
            new=_noop_run,
        ),
        patch("app.workers.case_lifecycle_worker.flags", create=True),
        patch(
            "app.core.feature_flags.flags.AI_ASSIST_AUTO_RUN",
            new=False,
            create=True,
        ),
    ):
        repo_cls.return_value = MagicMock()

        # Stub the signal-handler call so the test runs on platforms
        # where add_signal_handler isn't supported on the running loop.
        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler", lambda *a, **k: None):
            from app.workers.case_lifecycle_worker import main
            await main()

        build_assist.assert_not_called()


@pytest.mark.asyncio
async def test_main_builds_ai_service_when_flag_on() -> None:
    """Inverse of the above: when the flag is on, `main()` MUST build
    the AiAssistService and inject it into the sweeper.
    """
    fake_db = MagicMock(name="fake_db")
    captured: dict = {}

    async def _noop_run(self) -> None:
        captured["ai_assist"] = self._ai_assist
        captured["auto_run"] = self._auto_run

    with (
        patch(
            "app.workers.case_lifecycle_worker.connect_mongo",
            new=AsyncMock(),
        ),
        patch(
            "app.workers.case_lifecycle_worker.disconnect_mongo",
            new=AsyncMock(),
        ),
        patch(
            "app.workers.case_lifecycle_worker.get_db",
            return_value=fake_db,
        ),
        patch(
            "app.workers.case_lifecycle_worker.QuarantineCaseRepository"
        ),
        patch(
            "app.workers.case_lifecycle_worker._build_ai_assist_service"
        ) as build_assist,
        patch("app.workers.case_lifecycle_worker.configure_logging"),
        patch(
            "app.workers.case_lifecycle_worker._Sweeper.run",
            new=_noop_run,
        ),
        patch(
            "app.core.feature_flags.flags.AI_ASSIST_AUTO_RUN",
            new=True,
            create=True,
        ),
    ):
        sentinel_assist = MagicMock(name="ai_assist")
        build_assist.return_value = sentinel_assist

        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler", lambda *a, **k: None):
            from app.workers.case_lifecycle_worker import main
            await main()

        build_assist.assert_called_once_with(fake_db)
        assert captured["ai_assist"] is sentinel_assist
        assert captured["auto_run"] is True
