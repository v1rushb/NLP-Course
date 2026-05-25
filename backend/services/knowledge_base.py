import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from db.models import PpuKnowledgeChunk

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = int(os.getenv("PPU_KNOWLEDGE_MAX_RESULTS", "5"))
DEFAULT_MIN_SCORE = float(os.getenv("PPU_KNOWLEDGE_MIN_SCORE", "2.0"))
SYNC_INTERVAL_SECONDS = int(os.getenv("PPU_KNOWLEDGE_SYNC_INTERVAL_SECONDS", "300"))
INDEX_VERSION = "arabic-normalization-v2"
ELASTICSEARCH_INDEX_VERSION = "v1"

_last_sync_at = 0.0
_sync_in_progress = False
_sync_lock = threading.Lock()
_es_client: Any | None = None
_es_unavailable_until = 0.0

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
            sync_pdf_knowledge(db, sync_elasticsearch=False)
            if _elastic_enabled():
                retries = _env_int("ELASTICSEARCH_STARTUP_SYNC_RETRIES", 12)
                delay = _env_float("ELASTICSEARCH_STARTUP_RETRY_SECONDS", 5.0)
                for attempt in range(retries):
                    if sync_elasticsearch_knowledge(db):
                        break
                    if attempt < retries - 1:
                        time.sleep(delay)
            _last_sync_at = time.time()
    except Exception:
        logger.exception("Background PPU PDF knowledge indexing failed")
    finally:
        with _sync_lock:
            _sync_in_progress = False


def sync_pdf_knowledge(db: Session, sync_elasticsearch: bool = True) -> int:
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
    if sync_elasticsearch:
        sync_elasticsearch_knowledge(db)
    logger.info("PPU PDF knowledge index ready chunks=%s data_dir=%s", indexed, data_dir)
    return indexed


def sync_elasticsearch_knowledge(db: Session) -> bool:
    client = _elastic_client()
    if client is None:
        return False

    rows = db.scalars(select(PpuKnowledgeChunk).order_by(PpuKnowledgeChunk.id)).all()
    if not rows:
        return False

    try:
        _ensure_elastic_index(client)
        _clear_elastic_index(client)
        batch_size = _env_int("ELASTICSEARCH_SYNC_BATCH_SIZE", 500)
        for start in range(0, len(rows), batch_size):
            _bulk_index_elastic(client, rows[start:start + batch_size])
        response = client.post(
            _elastic_url(f"/{_elastic_index()}/_refresh"),
            timeout=_env_float("ELASTICSEARCH_TIMEOUT_SECONDS", 2.0),
        )
        response.raise_for_status()
        logger.info("Elasticsearch knowledge index synced chunks=%s index=%s", len(rows), _elastic_index())
        return True
    except Exception:
        _mark_elastic_unavailable()
        logger.exception("Elasticsearch knowledge sync failed; SQL knowledge search remains available")
        return False


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

    elastic_hits = _search_elasticsearch(query, query_norm, tokens, limit or DEFAULT_MAX_RESULTS)
    if elastic_hits:
        return elastic_hits

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


def _search_elasticsearch(
    query: str,
    query_norm: str,
    tokens: list[str],
    limit: int,
) -> list[KnowledgeHit]:
    client = _elastic_client()
    if client is None:
        return []

    keyword_query = " ".join(tokens)
    should = [
        {"match_phrase": {"search_text": {"query": query_norm, "boost": 5}}},
        {"match": {"search_text": {"query": keyword_query, "operator": "and", "boost": 4}}},
        {"match": {"text": {"query": query, "operator": "and", "boost": 3}}},
        {
            "multi_match": {
                "query": keyword_query,
                "fields": ["search_text^3", "text^2", "source"],
                "type": "best_fields",
                "minimum_should_match": "60%",
            }
        },
    ]

    try:
        response = client.post(
            _elastic_url(f"/{_elastic_index()}/_search"),
            json={
                "size": limit,
                "query": {"bool": {"should": should, "minimum_should_match": 1}},
                "_source": ["source", "page", "text"],
            },
            timeout=_env_float("ELASTICSEARCH_TIMEOUT_SECONDS", 2.0),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        _mark_elastic_unavailable()
        logger.exception("Elasticsearch knowledge search failed; falling back to SQL search")
        return []

    hits: list[KnowledgeHit] = []
    for hit in payload.get("hits", {}).get("hits", []):
        source = hit.get("_source") or {}
        text = source.get("text") or ""
        if not text:
            continue
        hits.append(KnowledgeHit(
            source=source.get("source") or "Elasticsearch",
            page=source.get("page"),
            text=text,
            score=float(hit.get("_score") or 0),
        ))
    return hits


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


def _elastic_client():
    if not _elastic_enabled():
        return None

    global _es_client
    if _es_client is not None:
        return _es_client

    if time.time() < _es_unavailable_until:
        return None

    try:
        import requests

        client = requests.Session()
        response = client.get(
            _elastic_url("/_cluster/health"),
            timeout=_env_float("ELASTICSEARCH_TIMEOUT_SECONDS", 2.0),
        )
        response.raise_for_status()
        _es_client = client
        return _es_client
    except Exception:
        _mark_elastic_unavailable()
        logger.warning("Elasticsearch is not reachable yet; using SQL knowledge search")
        return None


def _ensure_elastic_index(client) -> None:
    index = _elastic_index()
    response = client.head(
        _elastic_url(f"/{index}"),
        timeout=_env_float("ELASTICSEARCH_TIMEOUT_SECONDS", 2.0),
    )
    if response.status_code == 200:
        return
    if response.status_code != 404:
        response.raise_for_status()

    response = client.put(
        _elastic_url(f"/{index}"),
        json={
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "analysis": {
                    "analyzer": {
                        "ppu_arabic": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase", "decimal_digit", "arabic_normalization", "arabic_stop", "arabic_stemmer"],
                        }
                    },
                    "filter": {
                        "arabic_stop": {"type": "stop", "stopwords": "_arabic_"},
                        "arabic_stemmer": {"type": "stemmer", "language": "arabic"},
                    },
                },
            },
            "mappings": {
                "properties": {
                    "chunk_id": {"type": "integer"},
                    "source": {"type": "keyword"},
                    "source_path": {"type": "keyword", "index": False},
                    "file_hash": {"type": "keyword"},
                    "page": {"type": "integer"},
                    "chunk_index": {"type": "integer"},
                    "text": {
                        "type": "text",
                        "analyzer": "ppu_arabic",
                        "fields": {"standard": {"type": "text", "analyzer": "standard"}},
                    },
                    "search_text": {"type": "text", "analyzer": "standard"},
                    "indexed_at": {"type": "date"},
                    "index_version": {"type": "keyword"},
                }
            },
        },
        timeout=_env_float("ELASTICSEARCH_TIMEOUT_SECONDS", 2.0),
    )
    response.raise_for_status()


def _clear_elastic_index(client) -> None:
    response = client.post(
        _elastic_url(f"/{_elastic_index()}/_delete_by_query"),
        params={"conflicts": "proceed", "refresh": "true"},
        json={"query": {"match_all": {}}},
        timeout=_env_float("ELASTICSEARCH_TIMEOUT_SECONDS", 2.0),
    )
    response.raise_for_status()


def _bulk_index_elastic(client, rows: list[PpuKnowledgeChunk]) -> None:
    now = int(time.time() * 1000)
    lines: list[str] = []
    for row in rows:
        lines.append(json.dumps({"index": {"_index": _elastic_index(), "_id": row.id}}))
        lines.append(json.dumps(
            {
                "chunk_id": row.id,
                "source": row.source,
                "source_path": row.source_path,
                "file_hash": row.file_hash,
                "page": row.page,
                "chunk_index": row.chunk_index,
                "text": row.text,
                "search_text": row.search_text,
                "indexed_at": now,
                "index_version": ELASTICSEARCH_INDEX_VERSION,
            },
            ensure_ascii=False,
        ))

    response = client.post(
        _elastic_url("/_bulk"),
        data=("\n".join(lines) + "\n").encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
        timeout=_env_float("ELASTICSEARCH_TIMEOUT_SECONDS", 2.0),
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError("Elasticsearch bulk indexing reported item errors")


def _elastic_enabled() -> bool:
    return _truthy(os.getenv("ELASTICSEARCH_ENABLED", "false"))


def _elastic_index() -> str:
    return os.getenv("ELASTICSEARCH_INDEX", "ppu_knowledge")


def _elastic_url(path: str) -> str:
    base = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _mark_elastic_unavailable() -> None:
    global _es_client, _es_unavailable_until
    _es_client = None
    _es_unavailable_until = time.time() + _env_float("ELASTICSEARCH_RETRY_AFTER_SECONDS", 15.0)


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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
