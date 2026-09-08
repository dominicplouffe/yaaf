"""Named, administrator-managed credentials explicitly assigned to projects."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Secret


def assigned_credential(session: Session, credential_id: str) -> Secret:
    # Assignment and deletion lock the same row, so usage cannot appear between
    # the deletion check and commit. The FK also protects non-HTTP writers.
    row = session.scalar(select(Secret).where(Secret.id == credential_id).with_for_update())
    if row is not None and row.kind != "billing":
        raise HTTPException(status_code=422, detail="A service credential cannot be assigned for billing")
    if row is None or row.llm_mode is None or row.project_id is not None:
        raise HTTPException(status_code=422, detail="Select an LLM credential configured in Settings")
    return row
