import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from db.models import ChatMessage, ChatState, Course, Enrollment, Otp, PpuInfo, User
from services.ai_service import AIConfigurationError, AIMessage, AIProviderError, generate_ai_response
from services.email_service import send_email_otp
from services.knowledge_base import KnowledgeHit, format_knowledge_context, search_knowledge
from services.otp_service import generate_otp, make_otp_fields, otp_expired, verify_otp

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = int(os.getenv("CHAT_STATE_TTL_SECONDS", "900"))
HISTORY_LIMIT = int(os.getenv("CHAT_HISTORY_LIMIT", "12"))
GRADE_OTP_TTL_SECONDS = int(os.getenv("GRADE_OTP_TTL_SECONDS", "300"))

GRADE_KW = ["grade", "grades", "mark", "marks", "score", "scores", "result", "علامة", "علامتي", "علاماتي", "درجتي", "درجاتي", "نتيجتي", "نتائجي"]
ALL_GRADE_KW = ["grades", "all grades", "full grades", "علاماتي", "درجاتي", "كل علاماتي", "كامل علاماتي", "نتائجي"]
ENROLL_KW = ["enroll", "register", "add course", "سجل", "سجلني", "تسجيل", "اضف", "أضف"]
DROP_KW = ["drop", "remove", "delete course", "احذف", "حذف", "امسح", "شيل"]
LIST_KW = ["my courses", "my schedule", "موادي", "جدولي", "المسجلة"]
AVAILABLE_KW = ["available", "seats", "courses available", "مقاعد", "متاح", "المواد المتاحة"]
COURSE_INFO_KW = ["course", "doctor", "teacher", "instructor", "time", "schedule", "مادة", "دكتور", "موعد"]
ADMIN_KW = ["admin", "student management", "manage students", "إدارة الطلاب", "ادارة الطلاب"]


@dataclass
class IntentResult:
    intent: str
    course: Course | None = None
    confidence: float = 1.0


def handle_chat(query: str, user: User, db: Session, history: list[dict] | None = None) -> str:
    return ChatbotEngine(db).handle(query, user, history=history)


class ChatbotEngine:
    def __init__(self, db: Session):
        self.db = db
        self._last_query = ""

    def handle(self, query: str, user: User, history: list[dict] | None = None) -> str:
        query = query.strip()
        self._last_query = query
        if not query:
            return self._text("How can I help?", "كيف يمكنني مساعدتك؟")

        state = self._get_state(user.id)
        follow_up = self._resolve_pending(query, user, state)
        if follow_up:
            return follow_up

        intent = self._classify(query, state)
        logger.info("chat intent=%s user_id=%s", intent.intent, user.id)

        if intent.intent == "grade_lookup":
            return self._handle_grade_lookup(user, intent.course)
        if intent.intent == "enroll":
            return self._handle_enroll(user, intent.course)
        if intent.intent == "drop":
            return self._handle_drop(user, intent.course)
        if intent.intent == "my_courses":
            self._clear_state(user.id)
            return self._format_my_courses(user)
        if intent.intent == "available_courses":
            self._clear_state(user.id)
            return self._format_available_courses()
        if intent.intent == "course_info":
            return self._handle_course_info(user, intent.course, state)
        if intent.intent == "admin":
            self._clear_state(user.id)
            return self._admin_response(user)

        self._clear_pending(user.id, keep_entities=True)
        return self._llm_answer(query, user, history or self._recent_history(user.id), state)

    def _resolve_pending(self, query: str, user: User, state: ChatState | None) -> str | None:
        if not state or not state.pending_clarification:
            return None

        if state.current_intent == "grade_otp":
            if query.isdigit():
                return self._verify_grade_otp(query, user, state)
            if self._starts_new_topic(query):
                self._clear_grade_otps(user.id)
                self._clear_pending(user.id, keep_entities=True)
                return None
            return self._text(
                "Enter the 6-digit code sent to your email to view grades.",
                "أدخل رمز التحقق المرسل إلى بريدك لعرض العلامات.",
            )

        if self._starts_new_topic(query):
            self._clear_pending(user.id, keep_entities=True)
            return None

        course = self._find_course_in_text(query)
        if not course:
            return None

        if state.current_intent == "grade_lookup":
            return self._request_grade_otp(user, course)
        if state.current_intent == "enroll":
            self._clear_pending(user.id, keep_entities=True)
            return self._enroll_course(user, course)
        if state.current_intent == "drop":
            self._clear_pending(user.id, keep_entities=True)
            return self._drop_course(user, course)

        return None

    def _classify(self, query: str, state: ChatState | None) -> IntentResult:
        text = query.lower()
        course = self._find_course_in_text(query)

        if _has_any(text, GRADE_KW):
            return IntentResult("grade_lookup", course=course)
        if _has_any(text, DROP_KW):
            return IntentResult("drop", course=course)
        if _has_any(text, LIST_KW):
            return IntentResult("my_courses", course=course)
        if _has_any(text, AVAILABLE_KW):
            return IntentResult("available_courses", course=course)
        if _has_any(text, ENROLL_KW):
            return IntentResult("enroll", course=course)
        if _has_any(text, ADMIN_KW):
            return IntentResult("admin", course=course)
        if course or _has_any(text, COURSE_INFO_KW):
            return IntentResult("course_info", course=course)

        referenced_course = self._state_course(state)
        if referenced_course and any(word in text for word in ["it", "this", "teacher", "doctor", "time", "موعد", "الدكتور"]):
            return IntentResult("course_info", course=referenced_course)

        return IntentResult("general", course=course, confidence=0.4)

    def _handle_grade_lookup(self, user: User, course: Course | None) -> str:
        if not course:
            return self._request_grade_otp(user, None)
        self._remember_course(user.id, course)
        return self._request_grade_otp(user, course)

    def _handle_enroll(self, user: User, course: Course | None) -> str:
        if user.role != "student":
            return self._text("Course enrollment is available to students only.", "التسجيل متاح للطلاب فقط.")
        if not course:
            self._set_state(user.id, "enroll", "course")
            return self._text("Which course would you like to enroll in?", "أي مادة تريد تسجيلها؟")
        return self._enroll_course(user, course)

    def _handle_drop(self, user: User, course: Course | None) -> str:
        if user.role != "student":
            return self._text("Course changes are available to students only.", "تعديل المواد متاح للطلاب فقط.")
        if not course:
            self._set_state(user.id, "drop", "course")
            return self._text("Which course should I remove?", "أي مادة تريد حذفها؟")
        return self._drop_course(user, course)

    def _handle_course_info(self, user: User, course: Course | None, state: ChatState | None) -> str:
        selected = course or self._state_course(state)
        if not selected:
            return self._text("Which course do you mean?", "أي مادة تقصد؟")
        self._remember_course(user.id, selected)
        seats = self._available_seats(selected)
        return self._text(
            f"{selected.code} - {selected.name}: {selected.doctor}, {selected.days} at {selected.time}. Seats available: {seats}/{selected.capacity}.",
            f"{selected.code} - {selected.name}: {selected.doctor}، {selected.days} الساعة {selected.time}. المقاعد المتاحة: {seats}/{selected.capacity}.",
        )

    def _request_grade_otp(self, user: User, course: Course | None) -> str:
        if user.role != "student":
            return self._text("Grades are available to students only.", "عرض العلامات متاح للطلاب فقط.")

        if course:
            enrollment = self._enrollment(user.id, course.id)
            if not enrollment:
                return self._text(
                    f"You are not enrolled in {course.code} - {course.name}.",
                    f"أنت غير مسجل في مادة {course.code} - {course.name}.",
                )
            scope = "course"
            course_id = course.id
            state_entities = {"scope": scope, "course_id": course.id}
            target_ar = f"علامة مادة {course.code}"
        else:
            if not self._student_courses(user.id):
                return self._text(
                    "You do not have enrolled courses yet.",
                    "لا توجد لديك مواد مسجلة حاليا، لذلك لا توجد علامات لعرضها.",
                )
            scope = "all"
            course_id = None
            state_entities = {"scope": scope}
            target_ar = "علاماتك المسجلة"

        code = generate_otp()
        self._clear_grade_otps(user.id)
        fields = make_otp_fields(code, ttl=GRADE_OTP_TTL_SECONDS)
        self.db.add(Otp(
            purpose="chat_grade",
            user_id=user.id,
            course_id=course_id,
            scope=scope,
            **fields,
        ))

        if not send_email_otp(user.email, code):
            self.db.rollback()
            return self._text(
                "I could not send the verification code right now. Please try again shortly.",
                "تعذر إرسال رمز التحقق الآن. حاول مرة أخرى بعد قليل.",
            )

        self._set_state(user.id, "grade_otp", "otp", state_entities)
        self.db.commit()
        return self._text(
            "I sent a verification code to your email. Enter it here to view your grades.",
            f"أرسلت رمز تحقق إلى بريدك الجامعي. اكتب الرمز هنا، وسأعرض لك {target_ar} بأمان.",
        )

    def _verify_grade_otp(self, code: str, user: User, state: ChatState) -> str:
        record = self._latest_grade_otp(user.id)
        if not record:
            self._clear_pending(user.id, keep_entities=True)
            return self._text(
                "There is no active verification code. Request the grade again.",
                "لا يوجد رمز تحقق نشط. اطلب العلامة مرة أخرى.",
            )

        record_dict = self._otp_record_dict(record)
        if otp_expired(record_dict, ttl=GRADE_OTP_TTL_SECONDS):
            self._clear_grade_otps(user.id)
            self._clear_pending(user.id, keep_entities=True)
            self.db.commit()
            return self._text(
                "The verification code expired. Request the grade again.",
                "انتهت صلاحية رمز التحقق. اطلب العلامة مرة أخرى.",
            )

        if not verify_otp(record_dict, code):
            record.attempts = (record.attempts or 0) + 1
            self.db.commit()
            return self._text("The verification code is not correct.", "رمز التحقق غير صحيح.")

        scope = ((state.entities or {}).get("scope") or record.scope or "course").lower()
        if scope == "all":
            self._clear_grade_otps(user.id)
            self._clear_pending(user.id, keep_entities=False)
            self.db.commit()
            return self._format_all_grades(user)

        course_id = (state.entities or {}).get("course_id") or record.course_id
        course = self.db.get(Course, course_id)
        self._clear_grade_otps(user.id)
        if not course:
            self._clear_pending(user.id, keep_entities=False)
            self.db.commit()
            return self._text("I could not find that course.", "لم أجد هذه المادة.")

        self._set_state(user.id, "course_info", "", {"course_id": course.id})
        self.db.commit()
        return self._format_course_grade(user, course)

    def _format_course_grade(self, user: User, course: Course) -> str:
        enrollment = self._enrollment(user.id, course.id)
        if not enrollment:
            return self._text(
                f"You are not enrolled in {course.code} - {course.name}.",
                f"أنت غير مسجل في مادة {course.code} - {course.name}.",
            )
        if enrollment.grade is None:
            return self._text(
                f"The grade for {course.code} has not been set yet.",
                f"علامة مادة {course.code} - {course.name} لم يتم رصدها بعد.",
            )
        shown = _format_grade(enrollment.grade)
        return self._text(
            f"Your grade in {course.code} - {course.name}: {shown}.",
            f"علامتك في مادة {course.code} - {course.name}: {shown}.",
        )

    def _format_all_grades(self, user: User) -> str:
        rows = self._student_courses(user.id)
        if not rows:
            return self._text(
                "You do not have enrolled courses yet.",
                "لا توجد لديك مواد مسجلة حاليا، لذلك لا توجد علامات لعرضها.",
            )

        graded = [row for row in rows if row["grade"] is not None]
        if not graded:
            return "\n".join([
                "لم يتم رصد أي علامة لك حتى الآن.",
                "المواد المسجلة لديك موجودة، لكن العلامات لم تعتمد بعد:",
                *[f"- {row['code']} - {row['name']}: لم يتم رصد العلامة بعد." for row in rows],
            ])

        return "\n".join([
            "علاماتك المسجلة:",
            *[
                f"- {row['code']} - {row['name']}: {_format_grade(row['grade']) if row['grade'] is not None else 'لم يتم رصد العلامة بعد.'}"
                for row in rows
            ],
        ])

    def _format_my_courses(self, user: User) -> str:
        rows = self._student_courses(user.id)
        if not rows:
            return self._text("You do not have enrolled courses yet.", "لا توجد مواد مسجلة حالياً.")
        return "\n".join(["موادك المسجلة:", *[f"- {row['code']} - {row['name']}" for row in rows]])

    def _format_available_courses(self) -> str:
        courses = self.db.scalars(select(Course).order_by(Course.code)).all()
        return "\n".join([
            "المواد المتاحة:",
            *[f"- {course.code} - {course.name} ({self._available_seats(course)}/{course.capacity} مقعد)" for course in courses],
        ])

    def _enroll_course(self, user: User, course: Course) -> str:
        if self._enrollment(user.id, course.id):
            return self._text("You are already enrolled in this course.", "أنت مسجل في هذه المادة مسبقاً.")
        if self._available_seats(course) <= 0:
            return self._text("This course is full.", "هذه المادة ممتلئة.")

        self.db.add(Enrollment(user_id=user.id, course_id=course.id, grade=None))
        self._remember_course(user.id, course)
        self.db.commit()
        return self._text(
            f"You are now enrolled in {course.code} - {course.name}.",
            f"تم تسجيلك في {course.code} - {course.name}.",
        )

    def _drop_course(self, user: User, course: Course) -> str:
        enrollment = self._enrollment(user.id, course.id)
        if not enrollment:
            return self._text("You are not enrolled in this course.", "أنت غير مسجل في هذه المادة.")

        self.db.delete(enrollment)
        self._remember_course(user.id, course)
        self.db.commit()
        return self._text(
            f"{course.code} - {course.name} was removed from your schedule.",
            f"تم حذف {course.code} - {course.name} من جدولك.",
        )

    def _admin_response(self, user: User) -> str:
        if user.role != "admin":
            return self._text("Admin tools are restricted to admins.", "أدوات الإدارة مخصصة للأدمن فقط.")
        return self._text(
            "Use the Admin section to manage students, records, and grades.",
            "استخدم قسم الأدمن لإدارة الطلاب والسجلات والعلامات.",
        )

    def _llm_answer(self, query: str, user: User, history: list[dict], state: ChatState | None) -> str:
        messages = _history_to_messages(history)
        messages.append(AIMessage(role="user", content=query))
        knowledge_hits = search_knowledge(self.db, query)
        try:
            return generate_ai_response(messages, self._system_prompt(user, state, knowledge_hits))
        except AIConfigurationError:
            logger.exception("AI configuration failed")
            if knowledge_hits:
                return self._knowledge_fallback(knowledge_hits)
            return self._text(
                "The assistant is not configured correctly. Course and account actions still work.",
                "إعدادات المساعد غير مكتملة. ما زالت إجراءات المواد والحساب متاحة.",
            )
        except AIProviderError:
            logger.exception("AI provider failed")
            if knowledge_hits:
                return self._knowledge_fallback(knowledge_hits)
            return self._text(
                "I am having trouble reaching the assistant service. Course and account actions still work.",
                "أواجه مشكلة في الاتصال بخدمة المساعد. ما زالت إجراءات المواد والحساب متاحة.",
            )

    def _system_prompt(self, user: User, state: ChatState | None, knowledge_hits: list[KnowledgeHit] | None = None) -> str:
        context = {
            "user": {"id": user.id, "name": user.name, "role": user.role, "student_id": user.student_id},
            "dialogue_state": self._state_dict(state),
            "enrolled_courses": self._student_courses(user.id) if user.role == "student" else [],
            "available_courses": [
                {
                    "id": course.id,
                    "code": course.code,
                    "name": course.name,
                    "doctor": course.doctor,
                    "days": course.days,
                    "time": course.time,
                    "available_seats": self._available_seats(course),
                }
                for course in self.db.scalars(select(Course).order_by(Course.code)).all()
            ],
            "ppu_facts": [
                {"q": item.q, "a": item.a}
                for item in self.db.scalars(select(PpuInfo).order_by(PpuInfo.id)).all()
            ],
            "knowledge_context": [hit.as_context() for hit in knowledge_hits or []],
        }
        knowledge_guidance = (
            "\u0639\u0646\u062f \u0627\u0644\u0625\u062c\u0627\u0628\u0629 \u0639\u0646 \u0623\u0633\u0626\u0644\u0629 \u0627\u0644\u062c\u0627\u0645\u0639\u0629 \u0627\u0644\u0639\u0627\u0645\u0629 \u0623\u0648 \u0627\u0644\u0628\u0631\u0627\u0645\u062c \u0623\u0648 \u062f\u0644\u064a\u0644 \u0627\u0644\u0637\u0627\u0644\u0628\u060c "
            "\u0627\u0639\u062a\u0645\u062f \u0623\u0648\u0644\u0627 \u0639\u0644\u0649 knowledge_context \u0627\u0644\u0645\u0633\u062a\u062e\u0631\u062c \u0645\u0646 \u0645\u0644\u0641\u0627\u062a PDF. "
            "\u0627\u0630\u0643\u0631 \u0627\u0644\u0645\u0635\u062f\u0631 \u0623\u0648 \u0627\u0644\u0635\u0641\u062d\u0629 \u0639\u0646\u062f\u0645\u0627 \u064a\u0643\u0648\u0646 \u0630\u0644\u0643 \u0645\u0641\u064a\u062f\u0627\u060c "
            "\u0648\u0625\u0630\u0627 \u0644\u0645 \u064a\u0643\u0646 \u0627\u0644\u062f\u0644\u064a\u0644 \u0643\u0627\u0641\u064a\u0627 \u0641\u0642\u0644 \u0628\u0644\u0637\u0641 \u0623\u0646\u0643 \u0644\u0645 \u062a\u062c\u062f \u0625\u062c\u0627\u0628\u0629 \u0645\u0624\u0643\u062f\u0629 \u0641\u064a \u0623\u062f\u0644\u0629 \u0627\u0644\u062c\u0627\u0645\u0639\u0629. "
        )
        return (
            knowledge_guidance +
            "أنت المساعد الأكاديمي لجامعة بوليتكنك فلسطين. "
            "أجب بالعربية الفصيحة الدافئة، واجعل الرد موجزا وواضحا وجميلا. "
            "استخدم السياق المنظم فقط عند الحديث عن بيانات الطالب أو المواد أو التسجيل. "
            "لا تخترع علامات أو سجلات أو مقاعد أو بيانات طالب. "
            "إذا لم تكن العلامة مرصودة فاكتب: لم يتم رصد العلامة بعد، ولا تكتب null أبدا. "
            "احفظ نية الحوار إذا كان المستخدم يجيب عن سؤال أو رمز تحقق.\n\n"
            f"Structured context JSON:\n{json.dumps(context, ensure_ascii=False)}"
        )

    def _knowledge_fallback(self, knowledge_hits: list[KnowledgeHit]) -> str:
        return "\n\n".join([
            "\u0648\u062c\u062f\u062a \u0641\u064a \u0623\u062f\u0644\u0629 \u0627\u0644\u062c\u0627\u0645\u0639\u0629 \u0645\u0627 \u064a\u0633\u0627\u0639\u062f\u0643:",
            format_knowledge_context(knowledge_hits[:3]),
            "\u0642\u062f \u062a\u0643\u0648\u0646 \u0627\u0644\u0625\u062c\u0627\u0628\u0629 \u0623\u0639\u0644\u0627\u0647 \u0645\u0642\u062a\u0637\u0641\u0629 \u0645\u0646 \u0627\u0644\u062f\u0644\u064a\u0644 \u0645\u0628\u0627\u0634\u0631\u0629 \u0644\u0623\u0646 \u062e\u062f\u0645\u0629 \u0627\u0644\u0635\u064a\u0627\u063a\u0629 \u0627\u0644\u0630\u0643\u064a\u0629 \u063a\u064a\u0631 \u0645\u062a\u0627\u062d\u0629 \u0627\u0644\u0622\u0646.",
        ])

    def _student_courses(self, user_id: int) -> list[dict]:
        rows = []
        enrollments = self.db.scalars(select(Enrollment).where(Enrollment.user_id == user_id)).all()
        for enrollment in enrollments:
            course = self.db.get(Course, enrollment.course_id)
            if course:
                rows.append({
                    "course_id": course.id,
                    "code": course.code,
                    "name": course.name,
                    "doctor": course.doctor,
                    "days": course.days,
                    "time": course.time,
                    "grade": enrollment.grade,
                    "grade_display": _format_grade(enrollment.grade) if enrollment.grade is not None else "لم يتم رصد العلامة بعد",
                })
        return rows

    def _recent_history(self, user_id: int) -> list[dict]:
        messages = self.db.scalars(
            select(ChatMessage).where(ChatMessage.user_id == user_id).order_by(ChatMessage.ts.desc()).limit(HISTORY_LIMIT)
        ).all()
        return [
            {"role": item.role, "text": item.text, "ts": item.ts}
            for item in reversed(messages)
        ]

    def _get_state(self, user_id: int) -> ChatState | None:
        state = self.db.scalar(select(ChatState).where(ChatState.user_id == user_id))
        if not state:
            return None
        if state.expires_at < time.time():
            self.db.delete(state)
            self.db.commit()
            return None
        return state

    def _set_state(self, user_id: int, intent: str, pending: str, entities: dict | None = None):
        state = self.db.scalar(select(ChatState).where(ChatState.user_id == user_id))
        payload = {
            "current_intent": intent,
            "pending_clarification": pending,
            "entities": entities or {},
            "updated_at": time.time(),
            "expires_at": time.time() + STATE_TTL_SECONDS,
        }
        if state:
            for key, value in payload.items():
                setattr(state, key, value)
        else:
            self.db.add(ChatState(user_id=user_id, **payload))
        self.db.flush()

    def _remember_course(self, user_id: int, course: Course):
        state = self._get_state(user_id)
        entities = dict((state.entities if state else {}) or {})
        entities["course_id"] = course.id
        self._set_state(user_id, (state.current_intent if state else "course_info") or "course_info", "", entities)

    def _clear_pending(self, user_id: int, keep_entities: bool):
        state = self._get_state(user_id)
        if not state:
            return
        self._set_state(user_id, state.current_intent or "", "", state.entities if keep_entities else {})
        self.db.commit()

    def _clear_state(self, user_id: int):
        self.db.execute(delete(ChatState).where(ChatState.user_id == user_id))
        self.db.commit()

    def _latest_grade_otp(self, user_id: int) -> Otp | None:
        return self.db.scalar(
            select(Otp)
            .where(Otp.user_id == user_id, Otp.purpose.in_(["chat_grade", "grade"]))
            .order_by(Otp.created_at.desc())
            .limit(1)
        )

    def _clear_grade_otps(self, user_id: int):
        self.db.execute(delete(Otp).where(Otp.user_id == user_id, Otp.purpose.in_(["chat_grade", "grade"])))
        self.db.flush()

    def _state_course(self, state: ChatState | None) -> Course | None:
        course_id = ((state.entities if state else {}) or {}).get("course_id")
        return self.db.get(Course, course_id) if course_id else None

    def _state_dict(self, state: ChatState | None) -> dict:
        if not state:
            return {}
        return {
            "current_intent": state.current_intent,
            "pending_clarification": state.pending_clarification,
            "entities": state.entities or {},
            "updated_at": state.updated_at,
            "expires_at": state.expires_at,
        }

    def _starts_new_topic(self, query: str) -> bool:
        text = query.lower()
        return any(_has_any(text, words) for words in [ENROLL_KW, DROP_KW, LIST_KW, AVAILABLE_KW, ADMIN_KW])

    def _find_course_in_text(self, text: str) -> Course | None:
        normalized_text = _normalize_code(text)
        courses = self.db.scalars(select(Course)).all()
        for course in courses:
            if _normalize_code(course.code) in normalized_text or course.name.lower() in text.lower():
                return course

        tokens = re.findall(r"[A-Za-z]{0,4}\s*-?\s*\d{2,4}", text)
        for token in tokens:
            token_norm = _normalize_code(token)
            token_digits = _course_digits(token_norm)
            token_letters = _course_letters(token_norm)
            for course in courses:
                code_norm = _normalize_code(course.code)
                if token_digits != _course_digits(code_norm):
                    continue
                code_letters = _course_letters(code_norm)
                if not token_letters or code_letters.startswith(token_letters) or token_letters.startswith(code_letters[:1]):
                    return course
        return None

    def _available_seats(self, course: Course) -> int:
        count = self.db.scalar(select(func.count(Enrollment.id)).where(Enrollment.course_id == course.id)) or 0
        return max(0, course.capacity - count)

    def _enrollment(self, user_id: int, course_id: int) -> Enrollment | None:
        return self.db.scalar(select(Enrollment).where(Enrollment.user_id == user_id, Enrollment.course_id == course_id))

    def _otp_record_dict(self, record: Otp) -> dict:
        return {
            "otp_hash": record.otp_hash,
            "otp_salt": record.otp_salt,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "attempts": record.attempts,
        }

    def _prefers_english(self, user_or_query: Any = None) -> bool:
        text = user_or_query if isinstance(user_or_query, str) else self._last_query
        return bool(re.search(r"[A-Za-z]", text))

    def _text(self, english: str, arabic: str) -> str:
        return arabic


def _history_to_messages(history: list[dict]) -> list[AIMessage]:
    messages: list[AIMessage] = []
    for item in history[-HISTORY_LIMIT:]:
        role = item.get("role")
        text = item.get("text") or item.get("content")
        if role in {"user", "assistant"} and text:
            messages.append(AIMessage(role=role, content=text))
    return messages


def _has_any(text: str, keywords: list[str]) -> bool:
    tokens: list[str] | None = None
    for keyword in keywords:
        keyword = keyword.lower()
        if _has_arabic(keyword):
            if " " in keyword:
                if keyword in text:
                    return True
                continue
            if tokens is None:
                tokens = re.findall(r"[a-z0-9\u0600-\u06ff]+", text)
            if any(_arabic_keyword_matches(token, keyword) for token in tokens):
                return True
        elif keyword in text:
            return True
    return False


def _has_arabic(value: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", value))


def _arabic_keyword_matches(token: str, keyword: str) -> bool:
    variants = {token}
    if token.startswith("ال"):
        variants.add(token[2:])
    for prefix in ("و", "ف", "ب", "ك", "ل"):
        if token.startswith(prefix):
            variants.add(token[1:])
        if token.startswith(prefix + "ال"):
            variants.add(token[3:])

    if keyword in variants:
        return True
    return len(keyword) >= 4 and any(variant.startswith(keyword) for variant in variants)


def _normalize_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _course_digits(value: str) -> str:
    return "".join(re.findall(r"\d+", value))


def _course_letters(value: str) -> str:
    return "".join(re.findall(r"[A-Z]+", value.upper()))


def _format_grade(value: float | int | None) -> str:
    if value is None:
        return "لم يتم رصد العلامة بعد"
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")
