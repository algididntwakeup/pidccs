import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    sheet_id: Mapped[str] = mapped_column(String(36), ForeignKey("sheets.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="default_tenant")
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="default_user")

    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    step: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    sheet = relationship("Sheet", back_populates="jobs")
