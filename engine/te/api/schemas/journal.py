"""Mirrors the JournalEntry slice of dashboard/src/types/index.ts."""

from pydantic import BaseModel


class JournalEntry(BaseModel):
    date: str
    notes: str
    emotion: str
    tags: list[str]
    # Server-derived from that day's actual closed trades — never client-invented.
    pnl: float | None = None
