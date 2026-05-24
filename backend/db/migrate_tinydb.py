import json
from pathlib import Path

from sqlalchemy import select

from db.database import SessionLocal, init_db
from db.models import ChatMessage, ChatState, Course, Enrollment, Otp, PpuInfo, User


LEGACY_FILE = Path(__file__).resolve().parents[1] / "ppu.json"


def _items(table: dict | None) -> list[dict]:
    if not isinstance(table, dict):
        return []
    return [value for value in table.values() if isinstance(value, dict)]


def migrate(path: Path = LEGACY_FILE) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Legacy TinyDB file not found: {path}")

    init_db()
    data = json.loads(path.read_text(encoding="utf-8"))

    with SessionLocal() as db:
        for item in _items(data.get("users")):
            email = item.get("email")
            if not email or db.scalar(select(User).where(User.email == email)):
                continue
            db.add(
                User(
                    email=email,
                    password_hash=item.get("password_hash") or item.get("password") or "",
                    name=item.get("name") or email,
                    role=item.get("role") or "student",
                    student_id=item.get("student_id"),
                )
            )
        db.flush()

        user_by_legacy_id = {
            str(item.get("id")): db.scalar(select(User).where(User.email == item.get("email")))
            for item in _items(data.get("users"))
            if item.get("email")
        }

        for item in _items(data.get("courses")):
            code = item.get("code")
            if not code or db.scalar(select(Course).where(Course.code == code)):
                continue
            db.add(
                Course(
                    code=code,
                    name=item.get("name") or code,
                    doctor=item.get("doctor") or "",
                    days=item.get("days") or "",
                    time=item.get("time") or "",
                    capacity=int(item.get("capacity") or 0),
                )
            )
        db.flush()

        course_by_legacy_id = {
            str(item.get("id")): db.scalar(select(Course).where(Course.code == item.get("code")))
            for item in _items(data.get("courses"))
            if item.get("code")
        }

        for item in _items(data.get("enrollments")):
            user = user_by_legacy_id.get(str(item.get("user_id")))
            course = course_by_legacy_id.get(str(item.get("course_id")))
            if not user or not course:
                continue
            exists = db.scalar(
                select(Enrollment).where(
                    Enrollment.user_id == user.id,
                    Enrollment.course_id == course.id,
                )
            )
            if exists:
                continue
            db.add(
                Enrollment(
                    user_id=user.id,
                    course_id=course.id,
                    grade=item.get("grade"),
                    grade_updated_at=item.get("grade_updated_at"),
                    grade_updated_by=None,
                )
            )

        if not db.scalar(select(PpuInfo).limit(1)):
            for item in _items(data.get("ppu_info")):
                if item.get("q") and item.get("a"):
                    db.add(PpuInfo(q=item["q"], a=item["a"]))

        for item in _items(data.get("chat")):
            user = user_by_legacy_id.get(str(item.get("user_id")))
            if user and item.get("role") and item.get("text"):
                db.add(
                    ChatMessage(
                        user_id=user.id,
                        role=item["role"],
                        text=item["text"],
                        ts=float(item.get("ts") or 0),
                    )
                )

        for item in _items(data.get("chat_state")):
            user = user_by_legacy_id.get(str(item.get("user_id")))
            if user and not db.scalar(select(ChatState).where(ChatState.user_id == user.id)):
                db.add(
                    ChatState(
                        user_id=user.id,
                        current_intent=item.get("current_intent"),
                        pending_clarification=item.get("pending_clarification"),
                        entities=item.get("entities") or {},
                        updated_at=float(item.get("updated_at") or 0),
                        expires_at=float(item.get("expires_at") or 0),
                    )
                )

        for item in _items(data.get("otps")):
            user = user_by_legacy_id.get(str(item.get("user_id"))) if item.get("user_id") else None
            db.add(
                Otp(
                    purpose=item.get("purpose") or "registration",
                    user_id=user.id if user else None,
                    email=item.get("email"),
                    course_id=None,
                    scope=item.get("scope"),
                    pending_user=item.get("pending_user"),
                    otp_salt=item.get("otp_salt"),
                    otp_hash=item.get("otp_hash"),
                    created_at=float(item.get("created_at") or 0),
                    expires_at=float(item.get("expires_at") or 0),
                    attempts=int(item.get("attempts") or 0),
                    resend_available_at=item.get("resend_available_at"),
                )
            )

        db.commit()


if __name__ == "__main__":
    migrate()
