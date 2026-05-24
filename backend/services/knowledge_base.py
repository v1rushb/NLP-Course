import hashlib
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from db.models import PpuKnowledgeChunk

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = int(os.getenv("PPU_KNOWLEDGE_MAX_RESULTS", "5"))
DEFAULT_MIN_SCORE = float(os.getenv("PPU_KNOWLEDGE_MIN_SCORE", "2.0"))
SYNC_INTERVAL_SECONDS = int(os.getenv("PPU_KNOWLEDGE_SYNC_INTERVAL_SECONDS", "300"))
INDEX_VERSION = "arabic-normalization-v2"

_last_sync_at = 0.0
_sync_in_progress = False
_sync_lock = threading.Lock()

ARABIC_STOPWORDS = {
    "انا", "انت", "انتي", "هو", "هي", "هم", "هن", "هذا", "هذه", "ذلك", "تلك",
    "ما", "ماذا", "من", "الى", "إلى", "عن", "على", "في", "هل", "كيف", "كم",
    "اي", "أي", "اين", "أين", "متى", "لماذا", "الذي", "التي", "الذين", "مع",
    "او", "أو", "و", "يا", "لو", "اذا", "إذا", "كل", "بعض", "هناك", "لدي",
    "عندي", "اريد", "أريد", "بدي", "ممكن", "جامعة", "جامعه", "الجامعة",
    "الجامعه", "بوليتكنك", "فلسطين", "الطالب", "طالب", "طلبة", "طلبه",
}

ENGLISH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "my", "of", "on", "or", "ppu", "the", "to",
    "university", "what", "when", "where", "which", "who", "why", "with",
}


@dataclass
class KnowledgeHit:
    source: str
    page: int | None
    text: str
    score: float

    def as_context(self) -> dict:
        return {
            "source": self.source,
            "page": self.page,
            "score": round(self.score, 2),
            "text": self.text,
        }


def ensure_knowledge_index(db: Session) -> None:
    if not _truthy(os.getenv("PPU_KNOWLEDGE_AUTO_INDEX", "true")):
        return

    global _last_sync_at, _sync_in_progress
    if _sync_in_progress:
        return
    if _last_sync_at and time.time() - _last_sync_at < SYNC_INTERVAL_SECONDS:
        return

    with _sync_lock:
        if _sync_in_progress:
            return
        _sync_in_progress = True
    try:
        sync_pdf_knowledge(db)
        _last_sync_at = time.time()
    finally:
        with _sync_lock:
            _sync_in_progress = False


def start_background_knowledge_sync() -> None:
    if not _truthy(os.getenv("PPU_KNOWLEDGE_AUTO_INDEX", "true")):
        return

    global _sync_in_progress
    with _sync_lock:
        if _sync_in_progress:
            return
        _sync_in_progress = True

    thread = threading.Thread(target=_run_background_sync, name="ppu-knowledge-index", daemon=True)
    thread.start()


def _run_background_sync() -> None:
    global _last_sync_at, _sync_in_progress
    try:
        from db.database import SessionLocal

        with SessionLocal() as db:
            sync_pdf_knowledge(db)
            _last_sync_at = time.time()
    except Exception:
        logger.exception("Background PPU PDF knowledge indexing failed")
    finally:
        with _sync_lock:
            _sync_in_progress = False


def sync_pdf_knowledge(db: Session) -> int:
    data_dir = _data_dir()
    if not data_dir.exists():
        logger.warning("PPU knowledge data directory was not found: %s", data_dir)
        return 0

    pdfs = sorted(path for path in data_dir.glob("*.pdf") if path.is_file())
    if not pdfs:
        logger.warning("No PDF knowledge files found in %s", data_dir)
        return 0

    indexed = 0
    for pdf_path in pdfs:
        indexed += _sync_pdf(db, pdf_path)

    db.commit()
    logger.info("PPU PDF knowledge index ready chunks=%s data_dir=%s", indexed, data_dir)
    return indexed


def search_knowledge(
    db: Session,
    query: str,
    limit: int | None = None,
    min_score: float | None = None,
) -> list[KnowledgeHit]:
    ensure_knowledge_index(db)

    query_norm = normalize_for_search(query)
    tokens = _keywords(query_norm)
    if not tokens:
        return []

    rows = db.scalars(select(PpuKnowledgeChunk)).all()
    hits: list[KnowledgeHit] = []
    threshold = DEFAULT_MIN_SCORE if min_score is None else min_score
    for row in rows:
        score = _score(row.search_text or normalize_for_search(row.text), query_norm, tokens)
        if score >= threshold:
            hits.append(KnowledgeHit(
                source=row.source,
                page=row.page,
                text=row.text,
                score=score,
            ))

    hits.sort(key=lambda item: item.score, reverse=True)
    return hits[:limit or DEFAULT_MAX_RESULTS]


def format_knowledge_context(hits: list[KnowledgeHit]) -> str:
    if not hits:
        return ""

    sections = []
    for index, hit in enumerate(hits, start=1):
        page = f"، صفحة {hit.page}" if hit.page else ""
        sections.append(
            f"[{index}] المصدر: {hit.source}{page}\n{_trim(hit.text, 1400)}"
        )
    return "\n\n".join(sections)


def _sync_pdf(db: Session, pdf_path: Path) -> int:
    file_hash = _file_hash(pdf_path)
    source = pdf_path.name

    current_count = db.scalar(
        select(func.count(PpuKnowledgeChunk.id)).where(
            PpuKnowledgeChunk.source == source,
            PpuKnowledgeChunk.file_hash == file_hash,
        )
    ) or 0
    stale_count = db.scalar(
        select(func.count(PpuKnowledgeChunk.id)).where(
            PpuKnowledgeChunk.source == source,
            PpuKnowledgeChunk.file_hash != file_hash,
        )
    ) or 0
    if current_count and not stale_count:
        return current_count

    db.execute(delete(PpuKnowledgeChunk).where(PpuKnowledgeChunk.source == source))

    chunks = list(_extract_chunks(pdf_path))
    for index, (page, text) in enumerate(chunks):
        db.add(PpuKnowledgeChunk(
            source=source,
            source_path=str(pdf_path),
            file_hash=file_hash,
            page=page,
            chunk_index=index,
            text=text,
            search_text=normalize_for_search(text),
        ))

    logger.info("Indexed PPU PDF source=%s chunks=%s", source, len(chunks))
    return len(chunks)


def _extract_chunks(pdf_path: Path) -> list[tuple[int | None, str]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.exception("pypdf is required to index PDF knowledge files")
        return []

    reader = PdfReader(str(pdf_path))
    extracted: list[tuple[int | None, str]] = []
    is_faq = "faq" in pdf_path.name.lower()

    for page_index, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        cleaned = clean_pdf_text(raw)
        if not cleaned:
            continue

        if is_faq:
            extracted.extend((page_index, item) for item in _faq_chunks(cleaned))
        else:
            extracted.extend((page_index, item) for item in _page_chunks(cleaned))

    return extracted


def clean_pdf_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", text)
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_for_search(text: str) -> str:
    text = clean_pdf_text(text).lower()
    text = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", text)
    replacements = str.maketrans({
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ی": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
        "ھ": "ه",
        "ہ": "ه",
        "ۀ": "ه",
        "ک": "ك",
    })
    text = text.translate(replacements)
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _faq_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    for line in text.splitlines():
        line = line.strip(" \t،,")
        if not line or line.lower() == "question,answer":
            continue
        if "," not in line:
            continue
        question, answer = [part.strip() for part in line.split(",", 1)]
        if len(question) < 6 or len(answer) < 4:
            continue
        chunks.append(f"سؤال: {question}\nجواب: {answer}")
    return chunks or _page_chunks(text)


def _page_chunks(text: str, max_chars: int = 1200, overlap: int = 180) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    joined = "\n\n".join(paragraphs)
    if len(joined) <= max_chars:
        return [joined] if len(joined) >= 80 else []

    chunks: list[str] = []
    start = 0
    while start < len(joined):
        end = min(len(joined), start + max_chars)
        if end < len(joined):
            boundary = joined.rfind("\n", start, end)
            if boundary > start + 400:
                end = boundary
        chunk = joined[start:end].strip()
        if len(chunk) >= 80:
            chunks.append(chunk)
        if end >= len(joined):
            break
        start = max(0, end - overlap)
    return chunks


def _keywords(normalized_query: str) -> list[str]:
    stopwords = ARABIC_STOPWORDS | ENGLISH_STOPWORDS
    tokens = re.findall(r"[0-9a-z\u0600-\u06ff]+", normalized_query)
    return [
        token for token in tokens
        if len(token) > 1 and token not in stopwords
    ]


def _score(text: str, query_norm: str, tokens: list[str]) -> float:
    score = 0.0
    unique_tokens = set(tokens)

    if query_norm and query_norm in text:
        score += 12.0

    for token in unique_tokens:
        occurrences = text.count(token)
        if not occurrences:
            continue
        weight = 2.5 if len(token) >= 5 else 1.2
        score += weight + min(max(occurrences - 1, 0), 2) * 0.35

    if len(unique_tokens) >= 2:
        matched = sum(1 for token in unique_tokens if token in text)
        coverage = matched / len(unique_tokens)
        score += (coverage ** 2) * 10.0
        if matched == len(unique_tokens):
            score += 6.0

    for left, right in zip(tokens, tokens[1:]):
        if f"{left} {right}" in text:
            score += 3.0

    return score


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(INDEX_VERSION.encode("utf-8"))
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _data_dir() -> Path:
    configured = os.getenv("PPU_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()

    service_dir = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / "Data",
        Path.cwd().parent / "Data",
        service_dir.parent.parent / "Data",
        service_dir.parent.parent.parent / "Data",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _trim(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= max_chars else f"{text[:max_chars].rstrip()}..."


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}
