"""User model for authentication."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.project import gen_id


class User(Base):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=gen_id)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False, server_default="0", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship: projects owned by this user
    projects = relationship("Project", back_populates="owner", cascade="all, delete-orphan")
