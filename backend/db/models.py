from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from db.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="student", index=True)
    student_id = Column(String(64), nullable=True, index=True)

    enrollments = relationship(
        "Enrollment",
        foreign_keys="Enrollment.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    chat_messages = relationship("ChatMessage", back_populates="user", cascade="all, delete-orphan")
    chat_state = relationship("ChatState", back_populates="user", uselist=False, cascade="all, delete-orphan")
    otps = relationship("Otp", back_populates="user", cascade="all, delete-orphan")


class Course(Base):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    doctor = Column(String(255), nullable=True)
    days = Column(String(255), nullable=True)
    time = Column(String(64), nullable=True)
    capacity = Column(Integer, nullable=False, default=0)

    enrollments = relationship("Enrollment", back_populates="course", cascade="all, delete-orphan")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("user_id", "course_id", name="uq_enrollment_user_course"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    grade = Column(Float, nullable=True)
    grade_updated_at = Column(Float, nullable=True)
    grade_updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id], back_populates="enrollments")
    course = relationship("Course", back_populates="enrollments")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(32), nullable=False)
    text = Column(Text, nullable=False)
    ts = Column(Float, nullable=False)

    user = relationship("User", back_populates="chat_messages")


class ChatState(Base):
    __tablename__ = "chat_states"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    current_intent = Column(String(100), nullable=True)
    pending_clarification = Column(String(100), nullable=True)
    entities = Column(JSON, nullable=False, default=dict)
    updated_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=False)

    user = relationship("User", back_populates="chat_state")


class Otp(Base):
    __tablename__ = "otps"

    id = Column(Integer, primary_key=True, index=True)
    purpose = Column(String(64), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="SET NULL"), nullable=True)
    scope = Column(String(64), nullable=True)
    pending_user = Column(JSON, nullable=True)
    otp_salt = Column(String(128), nullable=True)
    otp_hash = Column(String(128), nullable=True)
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    resend_available_at = Column(Float, nullable=True)

    user = relationship("User", back_populates="otps")


class PpuInfo(Base):
    __tablename__ = "ppu_info"

    id = Column(Integer, primary_key=True, index=True)
    q = Column(String(255), nullable=False)
    a = Column(Text, nullable=False)


class PpuKnowledgeChunk(Base):
    __tablename__ = "ppu_knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("source", "file_hash", "chunk_index", name="uq_ppu_knowledge_source_hash_chunk"),
    )

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(255), nullable=False, index=True)
    source_path = Column(String(1024), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)
    page = Column(Integer, nullable=True, index=True)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    search_text = Column(Text, nullable=False)
