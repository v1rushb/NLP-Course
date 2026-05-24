import os
import time
import unittest

from sqlalchemy import delete, select

from db.database import SessionLocal, init_db
from db.models import ChatMessage, ChatState, Course, Enrollment, Otp, PpuKnowledgeChunk, User
from db.seed import seed_db
import services.chatbot_engine as chatbot_module
from services.chatbot_engine import ChatbotEngine
from services.knowledge_base import normalize_for_search


class ChatbotEngineTest(unittest.TestCase):
    def setUp(self):
        self.original_auto_index = os.environ.get("PPU_KNOWLEDGE_AUTO_INDEX")
        os.environ["PPU_KNOWLEDGE_AUTO_INDEX"] = "false"
        init_db()
        self.db = SessionLocal()
        seed_db(self.db)
        self.email = f"chat-test-{int(time.time() * 1000)}@example.com"
        self.user = User(
            email=self.email,
            password_hash="unused",
            name="Chat Test",
            role="student",
            student_id="999777",
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.cs101 = self.db.scalar(select(Course).where(Course.code == "CS101"))
        self.cs201 = self.db.scalar(select(Course).where(Course.code == "CS201"))
        self.assertIsNotNone(self.cs101)
        self.assertIsNotNone(self.cs201)
        self.engine = ChatbotEngine(self.db)
        self.sent_codes = {}
        self.original_send = chatbot_module.send_email_otp
        self.original_generate = chatbot_module.generate_otp
        self.original_ai_generate = chatbot_module.generate_ai_response
        chatbot_module.generate_otp = lambda: "123456"
        chatbot_module.send_email_otp = lambda email, code: self.sent_codes.setdefault(email, code) or True

    def tearDown(self):
        chatbot_module.send_email_otp = self.original_send
        chatbot_module.generate_otp = self.original_generate
        chatbot_module.generate_ai_response = self.original_ai_generate
        self.db.execute(delete(ChatMessage).where(ChatMessage.user_id == self.user.id))
        self.db.execute(delete(ChatState).where(ChatState.user_id == self.user.id))
        self.db.execute(delete(Otp).where(Otp.user_id == self.user.id))
        self.db.execute(delete(Enrollment).where(Enrollment.user_id == self.user.id))
        self.db.execute(delete(PpuKnowledgeChunk).where(PpuKnowledgeChunk.source == "unit-test-guide.pdf"))
        self.db.execute(delete(User).where(User.id == self.user.id))
        self.db.commit()
        self.db.close()
        if self.original_auto_index is None:
            os.environ.pop("PPU_KNOWLEDGE_AUTO_INDEX", None)
        else:
            os.environ["PPU_KNOWLEDGE_AUTO_INDEX"] = self.original_auto_index

    def enroll(self, course, grade=None):
        self.db.add(Enrollment(user_id=self.user.id, course_id=course.id, grade=grade))
        self.db.commit()

    def test_arabic_registered_courses_request_lists_courses(self):
        self.enroll(self.cs101)

        reply = self.engine.handle(
            "\u0645\u0627 \u0647\u064a \u0645\u0648\u0627\u062f\u064a \u0627\u0644\u0645\u0633\u062c\u0644\u0629\u061f",
            self.user,
            history=[],
        )

        self.assertIn("\u0645\u0648\u0627\u062f\u0643 \u0627\u0644\u0645\u0633\u062c\u0644\u0629", reply)
        self.assertIn("CS101", reply)
        self.assertNotIn("\u0623\u064a \u0645\u0627\u062f\u0629", reply)

    def test_full_grades_request_sends_otp_then_lists_grades(self):
        self.enroll(self.cs101, grade=88)
        self.enroll(self.cs201, grade=None)

        first = self.engine.handle("اعرض علاماتي كاملة", self.user, history=[])
        self.assertIn("رمز تحقق", first)

        verified = self.engine.handle("123456", self.user, history=[])
        self.assertIn("علاماتك المسجلة", verified)
        self.assertIn("CS101", verified)
        self.assertIn("88", verified)
        self.assertIn("CS201", verified)
        self.assertIn("لم يتم رصد العلامة بعد", verified)
        self.assertNotIn("null", verified.lower())

    def test_specific_grade_without_recorded_mark_is_not_null(self):
        self.enroll(self.cs201, grade=None)

        first = self.engine.handle("علامة CS201", self.user, history=[])
        self.assertIn("رمز تحقق", first)

        verified = self.engine.handle("123456", self.user, history=[])
        self.assertIn("CS201", verified)
        self.assertIn("لم يتم رصدها بعد", verified)
        self.assertNotIn("null", verified.lower())

    def test_ambiguous_enrollment_keeps_pending_intent(self):
        first = self.engine.handle("Enroll me", self.user, history=[])
        self.assertIn("أي مادة", first)

        follow_up = self.engine.handle("CS201", self.user, history=[])
        self.assertIn("تم تسجيلك", follow_up)
        enrolled = self.db.scalar(select(Enrollment).where(
            Enrollment.user_id == self.user.id,
            Enrollment.course_id == self.cs201.id,
        ))
        self.assertIsNotNone(enrolled)

    def test_switching_topics_clears_pending_clarification(self):
        self.engine.handle("Show my grade", self.user, history=[])
        reply = self.engine.handle("What courses are available?", self.user, history=[])

        self.assertIn("المواد المتاحة", reply)
        state = self.db.scalar(select(ChatState).where(ChatState.user_id == self.user.id))
        self.assertFalse((state and state.pending_clarification) or "")

    def test_course_reference_supports_follow_up_information(self):
        first = self.engine.handle("Tell me about CS101", self.user, history=[])
        self.assertIn("CS101", first)

        follow_up = self.engine.handle("Who teaches it?", self.user, history=[])
        self.assertIn(str(self.cs101.doctor), follow_up)

    def test_general_university_question_uses_pdf_knowledge_context(self):
        self.db.execute(delete(PpuKnowledgeChunk).where(PpuKnowledgeChunk.source == "unit-test-guide.pdf"))
        text = (
            "\u0633\u0624\u0627\u0644: \u0645\u0627 \u0647\u064a \u0648\u0627\u062d\u0629 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631 \u0627\u0644\u0623\u0643\u0627\u062f\u064a\u0645\u064a\u0629 \u0627\u0644\u0646\u0627\u062f\u0631\u0629\u061f\n"
            "\u062c\u0648\u0627\u0628: \u0648\u0627\u062d\u0629 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631 \u0645\u0648\u062c\u0648\u062f\u0629 \u0641\u064a \u0645\u0635\u062f\u0631 \u0627\u0644\u0648\u062d\u062f\u0629 \u0641\u0642\u0637"
        )
        self.db.add(PpuKnowledgeChunk(
            source="unit-test-guide.pdf",
            source_path="unit-test-guide.pdf",
            file_hash="unit",
            page=12,
            chunk_index=0,
            text=text,
            search_text=normalize_for_search(text),
        ))
        self.db.commit()
        captured = {}

        def fake_ai(messages, system):
            captured["system"] = system
            return "\u0648\u0627\u062d\u0629 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631 \u0645\u0648\u062c\u0648\u062f\u0629 \u0641\u064a \u0645\u0635\u062f\u0631 \u0627\u0644\u0648\u062d\u062f\u0629 \u0641\u0642\u0637."

        chatbot_module.generate_ai_response = fake_ai
        reply = self.engine.handle(
            "\u0645\u0627 \u0647\u064a \u0648\u0627\u062d\u0629 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631 \u0627\u0644\u0623\u0643\u0627\u062f\u064a\u0645\u064a\u0629 \u0627\u0644\u0646\u0627\u062f\u0631\u0629\u061f",
            self.user,
            history=[],
        )

        self.assertIn("\u0648\u0627\u062d\u0629 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631", reply)
        self.assertIn("knowledge_context", captured["system"])
        self.assertIn("unit-test-guide.pdf", captured["system"])
        self.assertIn("\u0648\u0627\u062d\u0629 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631", captured["system"])

    def test_pdf_knowledge_fallback_when_ai_provider_fails(self):
        self.db.execute(delete(PpuKnowledgeChunk).where(PpuKnowledgeChunk.source == "unit-test-guide.pdf"))
        text = (
            "\u0633\u0624\u0627\u0644: \u0645\u0627 \u0647\u064a \u0639\u0645\u0627\u062f\u0629 \u0627\u0644\u0642\u0628\u0648\u0644 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631\u064a\u0629\u061f\n"
            "\u062c\u0648\u0627\u0628: \u0639\u0645\u0627\u062f\u0629 \u0627\u0644\u0642\u0628\u0648\u0644 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631\u064a\u0629 \u062a\u0631\u0634\u062f \u0627\u0644\u0637\u0644\u0628\u0629 \u0625\u0644\u0649 \u0627\u0644\u0625\u062c\u0631\u0627\u0621 \u0627\u0644\u0645\u0646\u0627\u0633\u0628"
        )
        self.db.add(PpuKnowledgeChunk(
            source="unit-test-guide.pdf",
            source_path="unit-test-guide.pdf",
            file_hash="unit-fallback",
            page=18,
            chunk_index=1,
            text=text,
            search_text=normalize_for_search(text),
        ))
        self.db.commit()

        def failing_ai(messages, system):
            raise chatbot_module.AIProviderError("provider down")

        chatbot_module.generate_ai_response = failing_ai
        reply = self.engine.handle(
            "\u0645\u0627 \u0647\u064a \u0639\u0645\u0627\u062f\u0629 \u0627\u0644\u0642\u0628\u0648\u0644 \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631\u064a\u0629\u061f",
            self.user,
            history=[],
        )

        self.assertIn("\u0648\u062c\u062f\u062a \u0641\u064a \u0623\u062f\u0644\u0629 \u0627\u0644\u062c\u0627\u0645\u0639\u0629", reply)
        self.assertIn("unit-test-guide.pdf", reply)
        self.assertIn("\u0639\u0645\u0627\u062f\u0629 \u0627\u0644\u0642\u0628\u0648\u0644", reply)


if __name__ == "__main__":
    unittest.main()
