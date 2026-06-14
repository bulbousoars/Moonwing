from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from moonwing.db.models import AIModel


def _normalize(value: str) -> str:
    return value.strip().lower()


def upsert_discovered_models(
    db: Session,
    *,
    provider: str,
    execution_mode: str,
    model_ids: list[str],
    source: str,
    metadata: dict | None = None,
) -> list[AIModel]:
    provider = _normalize(provider)
    execution_mode = _normalize(execution_mode)
    now = datetime.now(UTC)
    rows: list[AIModel] = []
    for model_id in sorted({m.strip() for m in model_ids if m.strip()}):
        row = (
            db.query(AIModel)
            .filter(
                AIModel.provider == provider,
                AIModel.execution_mode == execution_mode,
                AIModel.model_id == model_id,
            )
            .first()
        )
        if row:
            row.source = source
            row.metadata_json = metadata or row.metadata_json or {}
            row.last_discovered_at = now
            row.updated_at = now
        else:
            row = AIModel(
                provider=provider,
                execution_mode=execution_mode,
                model_id=model_id,
                source=source,
                is_enabled=True,
                metadata_json=metadata or {},
                last_discovered_at=now,
                updated_at=now,
            )
            db.add(row)
        rows.append(row)
    db.flush()
    return rows


def list_enabled_models(db: Session, *, provider: str, execution_mode: str) -> list[str]:
    rows = (
        db.query(AIModel)
        .filter(
            AIModel.provider == _normalize(provider),
            AIModel.execution_mode == _normalize(execution_mode),
            AIModel.is_enabled.is_(True),
        )
        .order_by(AIModel.model_id)
        .all()
    )
    return [row.model_id for row in rows]


def replace_discovered_models(
    db: Session,
    *,
    provider: str,
    execution_mode: str,
    model_ids: list[str],
    source: str,
    metadata: dict | None = None,
) -> list[AIModel]:
    provider = _normalize(provider)
    execution_mode = _normalize(execution_mode)
    current = {m.strip() for m in model_ids if m.strip()}
    rows = upsert_discovered_models(
        db,
        provider=provider,
        execution_mode=execution_mode,
        model_ids=sorted(current),
        source=source,
        metadata=metadata,
    )
    stale_rows = (
        db.query(AIModel)
        .filter(
            AIModel.provider == provider,
            AIModel.execution_mode == execution_mode,
            AIModel.source == source,
            AIModel.model_id.notin_(current or {""}),
            AIModel.is_enabled.is_(True),
        )
        .all()
    )
    now = datetime.now(UTC)
    for row in stale_rows:
        row.is_enabled = False
        row.updated_at = now
    db.flush()
    return rows


def list_models(db: Session) -> list[AIModel]:
    return db.query(AIModel).order_by(AIModel.provider, AIModel.execution_mode, AIModel.model_id).all()


def set_model_enabled(
    db: Session,
    *,
    provider: str | None = None,
    execution_mode: str | None = None,
    model_id: str | None = None,
    model_uuid: UUID | None = None,
    enabled: bool,
) -> AIModel:
    query = db.query(AIModel)
    if model_uuid is not None:
        query = query.filter(AIModel.id == model_uuid)
    else:
        query = query.filter(
            AIModel.provider == _normalize(provider or ""),
            AIModel.execution_mode == _normalize(execution_mode or ""),
            AIModel.model_id == (model_id or "").strip(),
        )
    row = query.first()
    if not row:
        raise ValueError("AI model not found.")
    row.is_enabled = enabled
    row.updated_at = datetime.now(UTC)
    db.flush()
    return row
