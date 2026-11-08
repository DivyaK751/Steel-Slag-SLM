"""Read figures with Gemini on a background event loop, so parsing never waits on it."""

import asyncio
import logging
import threading
from collections.abc import Sequence
from concurrent.futures import Future
from dataclasses import dataclass

from google.adk.runners import Runner

from steel_slag_slm.agents.figure_reader import figure_label, read_figure
from steel_slag_slm.config import FigureReadingSettings
from steel_slag_slm.llm.costs import estimate_cost_usd
from steel_slag_slm.llm.prompts import PromptTemplate
from steel_slag_slm.schemas import FigureReading, FigureToRead

logger = logging.getLogger(__name__)
# This many figures failing in a row means Gemini itself is unreachable (login, quota, outage)
MAX_FAILURE_STREAK = 25


@dataclass(frozen=True, slots=True)
class PaperReadings:
    """One paper's readings, in the order its figures were given; None where a figure failed."""

    readings: list[FigureReading | None]
    cost_usd: float


class BackgroundFigureReader:
    """Send figures to Gemini from one background thread, under one call limit and budget.

    Owns the event loop that every call runs on, so the shared runner only ever sees one loop.
    Stops accepting work once the run's estimated cost reaches max_run_usd, or after
    MAX_FAILURE_STREAK figures fail in a row; unread figures are picked up by the next run.
    """

    def __init__(
        self,
        runner: Runner,
        prompt: PromptTemplate,
        settings: FigureReadingSettings,
        max_run_usd: float | None,
    ) -> None:
        self._runner = runner
        self._prompt = prompt
        self._settings = settings
        self._max_run_usd = max_run_usd
        self._loop = asyncio.new_event_loop()
        self._calls = asyncio.Semaphore(settings.max_concurrency)
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="figure-reader", daemon=True
        )
        self._thread.start()
        # Written on the background thread only; reads from the main thread see a recent value
        self.spent_usd = 0.0
        self.failure_streak = 0

    @property
    def stop_reason(self) -> str | None:
        """Why no more figures should be sent, or None while reading can go on."""
        if self._max_run_usd is not None and self.spent_usd >= self._max_run_usd:
            return f"reached the ${self._max_run_usd:.2f} Gemini budget for this run"
        if self.failure_streak >= MAX_FAILURE_STREAK:
            return f"{self.failure_streak} figures failed in a row; check Gemini access"
        return None

    def submit(
        self, figures: Sequence[FigureToRead], images: Sequence[bytes]
    ) -> Future[PaperReadings]:
        """Start reading one paper's figures; the future never raises for a single figure."""
        return asyncio.run_coroutine_threadsafe(self._read_paper(figures, images), self._loop)

    def close(self) -> None:
        """Stop the background loop, abandoning any reading still in progress."""
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()

    async def _read_paper(
        self, figures: Sequence[FigureToRead], images: Sequence[bytes]
    ) -> PaperReadings:
        results = await asyncio.gather(
            *(self._read_one(figure, png) for figure, png in zip(figures, images, strict=True))
        )
        return PaperReadings([reading for reading, _ in results], sum(cost for _, cost in results))

    async def _read_one(
        self, figure: FigureToRead, png: bytes
    ) -> tuple[FigureReading | None, float]:
        async with self._calls:
            if self.stop_reason is not None:
                return None, 0.0
            try:
                reading, usage = await asyncio.wait_for(
                    read_figure(self._runner, self._prompt, figure, png),
                    self._settings.call_timeout_s,
                )
            # Bad answers, API errors and timeouts cost this figure only; a rerun retries it
            except Exception as error:
                self.failure_streak += 1
                detail = f"{type(error).__name__}: {error}".removesuffix(": ")
                logger.warning(
                    "Could not read %s %s: %s", figure.paper_id, figure_label(figure), detail
                )
                return None, 0.0

        self.failure_streak = 0
        cost = estimate_cost_usd(usage, self._settings.prices)
        self.spent_usd += cost
        return reading, cost
