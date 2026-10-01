import uuid
from datetime import datetime
from typing import List

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    bima_user_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="engineer", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    projects: Mapped[List["Project"]] = relationship("Project", back_populates="owner")
    project_assignments: Mapped[List["ProjectAssignment"]] = relationship(
        "ProjectAssignment", back_populates="user", cascade="all, delete-orphan"
    )
    ground_truth_logs: Mapped[List["GroundTruthLog"]] = relationship(
        "GroundTruthLog", back_populates="user", cascade="all, delete-orphan"
    )
