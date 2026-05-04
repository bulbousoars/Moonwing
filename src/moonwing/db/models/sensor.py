from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base

JSON_DICT_VARIANT = MutableDict.as_mutable(JSON().with_variant(JSONB, 'postgresql'))
JSON_LIST_VARIANT = MutableList.as_mutable(JSON().with_variant(JSONB, 'postgresql'))


class SensorEndpoint(Base):
    __tablename__ = 'sensor_endpoints'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    os_name: Mapped[str] = mapped_column(String(255), nullable=False, default='', server_default='')
    sensor_version: Mapped[str] = mapped_column(String(64), nullable=False, default='', server_default='')
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='active', server_default='active')
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    labels: Mapped[list] = mapped_column(JSON_LIST_VARIANT, default=list, nullable=False)
    policy: Mapped[dict] = mapped_column(JSON_DICT_VARIANT, default=dict, nullable=False)
    inventory: Mapped[dict] = mapped_column(JSON_DICT_VARIANT, default=dict, nullable=False)
    network: Mapped[dict] = mapped_column(JSON_DICT_VARIANT, default=dict, nullable=False)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SensorTask(Base):
    __tablename__ = 'sensor_tasks'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    sensor_id: Mapped[UUID] = mapped_column(ForeignKey('sensor_endpoints.id'), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='queued', server_default='queued', index=True)
    payload: Mapped[dict] = mapped_column(JSON_DICT_VARIANT, default=dict, nullable=False)
    result: Mapped[dict] = mapped_column(JSON_DICT_VARIANT, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SensorEvent(Base):
    __tablename__ = 'sensor_events'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    sensor_id: Mapped[UUID] = mapped_column(ForeignKey('sensor_endpoints.id'), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default='info', server_default='info', index=True)
    payload: Mapped[dict] = mapped_column(JSON_DICT_VARIANT, default=dict, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
