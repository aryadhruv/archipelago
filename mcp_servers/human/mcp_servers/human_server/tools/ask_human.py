from functools import cache
from typing import Annotated

from pydantic import Field
from sim import SimulatedHuman
from utils.config import load_config


@cache
def _human() -> SimulatedHuman:
    # One per server process, which lives for the whole run.
    return SimulatedHuman(load_config())


async def ask_human(
    question: Annotated[
        str,
        Field(description="Your question, with the context they need to answer it."),
    ],
) -> str:
    """Ask the person who gave you this task a question. They answer briefly, and may point you to a file or share one with you."""
    return await _human().ask(question)
