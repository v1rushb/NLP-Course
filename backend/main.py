import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from db.database import init_db
from db.seed import seed
from routes import admin, auth, chat, courses
from services.knowledge_base import start_background_knowledge_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="PPU Assistant API")


def _cors_origins() -> list[str]:
    configured = os.getenv("FRONTEND_URL", "http://localhost:5173")
    origins = [item.strip() for item in configured.split(",") if item.strip()]
    return origins or ["http://localhost:5173"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(courses.router)
app.include_router(admin.router)


@app.on_event("startup")
def _startup():
    init_db()
    seed()
    start_background_knowledge_sync()


@app.get("/")
def root():
    return {"message": "PPU Assistant API is running"}
