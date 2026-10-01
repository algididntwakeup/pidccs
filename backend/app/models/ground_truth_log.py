import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base


class GroundTruthLog(Base):
    __tablename__ = "ground_truth_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    sheet_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("sheets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    user: Mapped["User"] = relationship("User", back_populates="ground_truth_logs")
    project: Mapped["Project"] = relationship("Project", back_populates="ground_truth_logs")
    sheet: Mapped[Optional["Sheet"]] = relationship("Sheet", back_populates="ground_truth_logs")
