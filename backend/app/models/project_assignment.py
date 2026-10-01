from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base


class ProjectAssignment(Base):
    __tablename__ = "project_assignments"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_assignment_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), default="bima", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="assignments")
    user = relationship("User", back_populates="project_assignments")
