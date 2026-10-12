from typing import Optional, Dict, Any
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="default_tenant")
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="default_user")
    owner_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    bima_project_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)
    # Flat catalog number from BIMA (e.g. BCD3-605-42). BIMA assigns no users;
    # the importer becomes the owner of the local row.
    project_number: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Line List operating data payload
    linelist_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    sheets = relationship("Sheet", back_populates="project", cascade="all, delete-orphan", lazy="selectin")
    owner = relationship("User", back_populates="projects")
    ground_truth_logs = relationship("GroundTruthLog", back_populates="project", cascade="all, delete-orphan")
