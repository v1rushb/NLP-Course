import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.database import SessionLocal
from db.models import Course, Enrollment, PpuInfo, User
from services.passwords import hash_password


PPU_FACTS = [
    {
        "q": "location",
        "a": "Palestine Polytechnic University is located in Hebron, Palestine.",
    },
    {
        "q": "academic programs",
        "a": "PPU offers undergraduate and graduate programs across engineering, IT, applied sciences, business, applied professions, and medicine.",
    },
    {
        "q": "registration",
        "a": "Admission and registration information is available from the Deanship of Admission and Registration.",
    },
    {
        "q": "library",
        "a": "The university library provides academic resources and student services.",
    },
]

COURSES = [
    ("CS101", "Introduction to Programming", "Dr. Ahmad Qunaibi", "Sunday and Tuesday", "10:00", 30),
    ("CS201", "Data Structures", "Dr. Manal Shaheen", "Monday and Wednesday", "12:00", 25),
    ("MATH102", "Calculus II", "Dr. Sami Odeh", "Sunday, Tuesday and Thursday", "08:00", 40),
    ("IT301", "Databases", "Dr. Laila Nimer", "Monday and Wednesday", "14:00", 20),
]


def seed() -> None:
    with SessionLocal() as db:
        seed_db(db)


def seed_db(db: Session) -> None:
    if not db.scalar(select(PpuInfo).limit(1)):
        db.add_all(PpuInfo(q=item["q"], a=item["a"]) for item in PPU_FACTS)

    for code, name, doctor, days, time, capacity in COURSES:
        if not db.scalar(select(Course).where(Course.code == code)):
            db.add(Course(
                code=code,
                name=name,
                doctor=doctor,
                days=days,
                time=time,
                capacity=capacity,
            ))

    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if admin_email and admin_password and not db.scalar(select(User).where(User.email == admin_email.lower())):
        db.add(User(
            email=admin_email.lower(),
            password_hash=hash_password(admin_password),
            name=os.getenv("ADMIN_NAME", "Administrator"),
            role="admin",
            student_id=os.getenv("ADMIN_STUDENT_ID"),
        ))

    if _truthy(os.getenv("SEED_DEV_DATA", "false")):
        _seed_dev_users(db)

    db.commit()


def _seed_dev_users(db: Session) -> None:
    dev_students = [
        ("220002@ppu.edu.ps", "student123", "Sara Khaled", "220002"),
        ("220003@ppu.edu.ps", "student123", "Mahmoud Ali", "220003"),
    ]
    for email, password, name, student_id in dev_students:
        if not db.scalar(select(User).where(User.email == email)):
            db.add(User(
                email=email,
                password_hash=hash_password(password),
                name=name,
                role="student",
                student_id=student_id,
            ))
    db.flush()

    first_student = db.scalar(select(User).where(User.email == "220002@ppu.edu.ps"))
    cs101 = db.scalar(select(Course).where(Course.code == "CS101"))
    math = db.scalar(select(Course).where(Course.code == "MATH102"))
    if first_student and cs101 and not _enrolled(db, first_student.id, cs101.id):
        db.add(Enrollment(user_id=first_student.id, course_id=cs101.id, grade=88))
    if first_student and math and not _enrolled(db, first_student.id, math.id):
        db.add(Enrollment(user_id=first_student.id, course_id=math.id, grade=75))


def _enrolled(db: Session, user_id: int, course_id: int) -> bool:
    return bool(db.scalar(
        select(Enrollment).where(
            Enrollment.user_id == user_id,
            Enrollment.course_id == course_id,
        )
    ))


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}
