import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import ChatMessage, Course, Enrollment, User
from services.auth_tokens import require_admin

router = APIRouter(prefix="/admin")


class GradeUpdateReq(BaseModel):
    admin_id: int | None = None
    course_id: int
    grade: float | None = None


def _ensure_admin(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user or user.role != "admin":
        raise HTTPException(403, "Admin access is required.")
    return user


def _safe_user(user: User):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "student_id": user.student_id,
    }


def _course_with_counts(db: Session, course: Course):
    enrollments = db.scalars(select(Enrollment).where(Enrollment.course_id == course.id)).all()
    students = []
    for enrollment in enrollments:
        user = db.get(User, enrollment.user_id)
        if user:
            students.append({
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "student_id": user.student_id,
                "grade": enrollment.grade,
            })
    return {
        "id": course.id,
        "code": course.code,
        "name": course.name,
        "doctor": course.doctor,
        "days": course.days,
        "time": course.time,
        "capacity": course.capacity,
        "enrolled_count": len(enrollments),
        "available_seats": max(0, course.capacity - len(enrollments)),
        "students": students,
    }


def _student_detail(db: Session, student: User):
    enrollments = db.scalars(select(Enrollment).where(Enrollment.user_id == student.id)).all()
    courses = []
    grades = []
    for enrollment in enrollments:
        course = db.get(Course, enrollment.course_id)
        if not course:
            continue
        grade = enrollment.grade
        if grade is not None:
            grades.append(float(grade))
        courses.append({
            "enrollment_id": enrollment.id,
            "course_id": course.id,
            "code": course.code,
            "name": course.name,
            "doctor": course.doctor,
            "days": course.days,
            "time": course.time,
            "grade": grade,
        })

    average = round(sum(grades) / len(grades), 2) if grades else None
    return {
        "profile": _safe_user(student),
        "enrolled_courses": courses,
        "grades": [
            {"course_id": c["course_id"], "code": c["code"], "grade": c["grade"]}
            for c in courses
        ],
        "academic_records": {
            "enrolled_courses": len(courses),
            "graded_courses": len(grades),
            "average_grade": average,
            "standing": "Good standing" if average is None or average >= 60 else "Needs review",
        },
    }


@router.get("/overview")
def overview(
    admin_id: int | None = None,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.scalars(select(User).order_by(User.id)).all()
    courses = [_course_with_counts(db, course) for course in db.scalars(select(Course).order_by(Course.code)).all()]
    enrollments = db.scalars(select(Enrollment)).all()

    return {
        "users": [_safe_user(user) for user in users],
        "courses": courses,
        "enrollments": [
            {"id": item.id, "user_id": item.user_id, "course_id": item.course_id, "grade": item.grade}
            for item in enrollments
        ],
        "chat_messages_count": db.scalar(select(func.count(ChatMessage.id))) or 0,
        "totals": {
            "users": len(users),
            "students": len([u for u in users if u.role == "student"]),
            "courses": len(courses),
            "enrollments": len(enrollments),
        },
    }


@router.get("/students")
def students(
    admin_id: int | None = None,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    student_users = db.scalars(
        select(User).where(User.role == "student").order_by(User.student_id, User.email)
    ).all()
    return [_student_detail(db, student) for student in student_users]


@router.put("/students/{student_id}/grades")
def update_student_grade(
    student_id: int,
    req: GradeUpdateReq,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    student = db.get(User, student_id)
    if not student or student.role != "student":
        raise HTTPException(404, "Student was not found.")

    if req.grade is not None and (req.grade < 0 or req.grade > 100):
        raise HTTPException(400, "Grade must be between 0 and 100.")

    course = db.get(Course, req.course_id)
    if not course:
        raise HTTPException(404, "Course was not found.")

    enrollment = db.scalar(select(Enrollment).where(
        Enrollment.user_id == student_id,
        Enrollment.course_id == req.course_id,
    ))
    if not enrollment:
        raise HTTPException(404, "Student is not enrolled in this course.")

    enrollment.grade = None if req.grade is None else round(float(req.grade), 2)
    enrollment.grade_updated_at = time.time()
    enrollment.grade_updated_by = current_admin.id
    db.commit()
    db.refresh(student)
    return {"ok": True, "student": _student_detail(db, student)}
