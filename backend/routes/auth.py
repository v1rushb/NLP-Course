import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import Otp, User
from services.auth_tokens import create_access_token
from services.email_service import send_email_otp
from services.otp_service import generate_otp, make_otp_fields, otp_expired, verify_otp
from services.passwords import hash_password, verify_password

router = APIRouter()

SIGNUP_OTP_TTL = 600
SIGNUP_RESEND_SECONDS = 60
SIGNUP_MAX_ATTEMPTS = 5


class SignupReq(BaseModel):
    email: str
    password: str
    name: str
    student_id: str | None = None


class SignupOtpReq(BaseModel):
    email: str
    otp: str


class SignupResendReq(BaseModel):
    email: str


class LoginReq(BaseModel):
    email: str
    password: str


def _email(value: str) -> str:
    return value.strip().lower()


def _public(user: User):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "student_id": user.student_id,
    }


def _auth_response(user: User):
    return {"user": _public(user), "token": create_access_token(user)}


def _latest_registration(db: Session, email: str) -> Otp | None:
    return db.scalar(
        select(Otp)
        .where(Otp.purpose == "registration", Otp.email == email)
        .order_by(Otp.created_at.desc())
        .limit(1)
    )


def _clear_registration(db: Session, email: str):
    db.execute(delete(Otp).where(Otp.purpose == "registration", Otp.email == email))


def _validate_signup(req: SignupReq, email: str):
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "Please enter a valid email address.")
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if not req.name.strip():
        raise HTTPException(400, "Name is required.")


def _send_registration_otp(email: str) -> dict:
    code = generate_otp()
    fields = make_otp_fields(code, ttl=SIGNUP_OTP_TTL)
    if not send_email_otp(email, code):
        raise HTTPException(503, "Could not send the verification code. Please try again.")
    return fields


def _otp_record_dict(record: Otp) -> dict:
    return {
        "otp_hash": record.otp_hash,
        "otp_salt": record.otp_salt,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "attempts": record.attempts,
    }


@router.post("/signup")
def signup(req: SignupReq, db: Session = Depends(get_db)):
    email = _email(req.email)
    _validate_signup(req, email)

    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(400, "Email is already registered.")

    pending_user = {
        "email": email,
        "password_hash": hash_password(req.password),
        "name": req.name.strip(),
        "role": "student",
        "student_id": (req.student_id or "").strip() or None,
    }

    _clear_registration(db, email)
    fields = _send_registration_otp(email)
    db.add(Otp(
        purpose="registration",
        email=email,
        pending_user=pending_user,
        resend_available_at=time.time() + SIGNUP_RESEND_SECONDS,
        **fields,
    ))
    db.commit()

    return {
        "pending": True,
        "email": email,
        "expires_in": SIGNUP_OTP_TTL,
        "resend_after": SIGNUP_RESEND_SECONDS,
    }


@router.post("/signup/verify")
def verify_signup(req: SignupOtpReq, db: Session = Depends(get_db)):
    email = _email(req.email)
    record = _latest_registration(db, email)
    if not record:
        raise HTTPException(404, "No pending registration was found for this email.")

    if otp_expired(_otp_record_dict(record), ttl=SIGNUP_OTP_TTL):
        _clear_registration(db, email)
        db.commit()
        raise HTTPException(400, "Verification code expired. Please register again.")

    attempts = int(record.attempts or 0)
    if attempts >= SIGNUP_MAX_ATTEMPTS:
        _clear_registration(db, email)
        db.commit()
        raise HTTPException(429, "Too many invalid attempts. Please register again.")

    otp = req.otp.strip()
    if len(otp) != 6 or not otp.isdigit() or not verify_otp(_otp_record_dict(record), otp):
        record.attempts = attempts + 1
        db.commit()
        raise HTTPException(400, "Invalid verification code.")

    if db.scalar(select(User).where(User.email == email)):
        _clear_registration(db, email)
        db.commit()
        raise HTTPException(400, "Email is already registered.")

    pending = record.pending_user or {}
    user = User(
        email=pending["email"],
        password_hash=pending["password_hash"],
        name=pending["name"],
        role=pending.get("role", "student"),
        student_id=pending.get("student_id"),
    )
    db.add(user)
    _clear_registration(db, email)
    db.commit()
    db.refresh(user)
    return _auth_response(user)


@router.post("/signup/resend")
def resend_signup_otp(req: SignupResendReq, db: Session = Depends(get_db)):
    email = _email(req.email)
    record = _latest_registration(db, email)
    if not record:
        raise HTTPException(404, "No pending registration was found for this email.")

    if otp_expired(_otp_record_dict(record), ttl=SIGNUP_OTP_TTL):
        _clear_registration(db, email)
        db.commit()
        raise HTTPException(400, "Verification code expired. Please register again.")

    wait = max(0, int((record.resend_available_at or 0) - time.time()))
    if wait > 0:
        raise HTTPException(429, {"message": "Please wait before requesting another code.", "retry_after": wait})

    fields = _send_registration_otp(email)
    record.otp_hash = fields["otp_hash"]
    record.otp_salt = fields["otp_salt"]
    record.created_at = fields["created_at"]
    record.expires_at = fields["expires_at"]
    record.attempts = 0
    record.resend_available_at = time.time() + SIGNUP_RESEND_SECONDS
    db.commit()

    return {
        "pending": True,
        "email": email,
        "expires_in": SIGNUP_OTP_TTL,
        "resend_after": SIGNUP_RESEND_SECONDS,
    }


@router.post("/login")
def login(req: LoginReq, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == _email(req.email)))
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password.")
    return _auth_response(user)
