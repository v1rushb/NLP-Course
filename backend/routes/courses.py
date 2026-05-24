from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import Course, Enrollment, User


router = APIRouter()


class EnrollmentReq(BaseModel):
    user_id: int
    course_id: int


def _course_payload(db: Session, course: Course, grade: float | None = None):
    count = db.scalar(select(func.count(Enrollment.id)).where(Enrollment.course_id == course.id)) or 0
    payload = {
        "id": course.id,
        "code": course.code,
        "name": course.name,
        "doctor": course.doctor,
        "days": course.days,
        "time": course.time,
        "capacity": course.capacity,
        "enrolled_count": count,
        "available_seats": max(0, course.capacity - count),
    }
    if grade is not None:
        payload["grade"] = grade
    return payload


def _ensure_student(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Student was not found.")
    if user.role != "student":
        raise HTTPException(403, "Only students can manage course enrollment.")
    return user


def _get_course(db: Session, course_id: int) -> Course:
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Course was not found.")
    return course


@router.get("/courses")
def list_courses(db: Session = Depends(get_db)):
    courses = db.scalars(select(Course).order_by(Course.code)).all()
    return [_course_payload(db, course) for course in courses]


@router.get("/my_courses")
def my_courses(user_id: int, db: Session = Depends(get_db)):
    _ensure_student(db, user_id)
    enrollments = db.scalars(select(Enrollment).where(Enrollment.user_id == user_id)).all()
    out = []
    for enrollment in enrollments:
        course = db.get(Course, enrollment.course_id)
        if course:
            payload = _course_payload(db, course)
            payload["grade"] = enrollment.grade
            out.append(payload)
    return out


@router.post("/enrollments")
def enroll(req: EnrollmentReq, db: Session = Depends(get_db)):
    _ensure_student(db, req.user_id)
    course = _get_course(db, req.course_id)

    existing = db.scalar(select(Enrollment).where(
        Enrollment.user_id == req.user_id,
        Enrollment.course_id == req.course_id,
    ))
    if existing:
        raise HTTPException(409, "You are already enrolled in this course.")

    if _course_payload(db, course)["available_seats"] <= 0:
        raise HTTPException(400, "This course is full.")

    enrollment = Enrollment(user_id=req.user_id, course_id=req.course_id, grade=None)
    db.add(enrollment)
    db.commit()
    return {"ok": True, "course": {**_course_payload(db, course), "grade": None}}


@router.delete("/enrollments")
def drop(req: EnrollmentReq, db: Session = Depends(get_db)):
    _ensure_student(db, req.user_id)
    course = _get_course(db, req.course_id)
    enrollment = db.scalar(select(Enrollment).where(
        Enrollment.user_id == req.user_id,
        Enrollment.course_id == req.course_id,
    ))
    if not enrollment:
        raise HTTPException(404, "You are not enrolled in this course.")

    db.delete(enrollment)
    db.commit()
    return {"ok": True, "course": _course_payload(db, course)}
