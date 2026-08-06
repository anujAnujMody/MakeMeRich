from fastapi import APIRouter, Response

from te.api.provenance import set_provenance
from te.api.schemas.journal import JournalEntry

router = APIRouter(prefix="/api/journal", tags=["journal"])


@router.get("", response_model=list[JournalEntry])
def list_journal(response: Response, tag: str | None = None) -> list[JournalEntry]:
    """Trade-journal entries, optionally filtered by `tag`. Empty until
    journal entries are recorded."""
    set_provenance(response, not_ready_reason="phase-0: no journal entries recorded yet")
    return []


@router.post("/save", response_model=JournalEntry, status_code=201)
def save_journal_entry(entry: JournalEntry, response: Response) -> JournalEntry:
    """Saves one journal entry and echoes it back. The journal is not
    persisted yet, so the entry is validated and returned but not stored."""
    set_provenance(response, not_ready_reason="phase-0: journal is not persisted yet")
    return entry
