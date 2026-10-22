"""Explicit, page-aware PDF ingestion. Publishes a complete corpus atomically."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader

from .documents import document_dir, get_client, index_name


CHUNK_SIZE = 1200
CHUNK_OVERLAP = 160
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_CORPUS_CHUNKS = 20000
INGESTOR_VERSION = "1"


def chunk_text(text: str) -> list[str]:
    text = " ".join(text.split())
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + CHUNK_SIZE // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def load_corpus(directory: Path) -> list[dict]:
    files = sorted(directory.rglob("*.pdf"))
    if not files:
        raise ValueError("No banking PDFs found in DOCUMENT_DIR")
    chunks = []
    document_ids = set()
    for path in files:
        if path.stat().st_size > MAX_PDF_BYTES:
            raise ValueError("PDF exceeds 25 MiB limit")
        raw = path.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        sidecar = path.with_suffix(".json")
        meta = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        allowed = {"document_id", "title", "version", "effective_date", "fictional", "applicability", "rules"}
        if set(meta) - allowed:
            raise ValueError("Unsupported PDF sidecar fields")
        if any(not isinstance(meta[k], str) or not meta[k].strip() for k in ("document_id", "title", "version", "applicability") if k in meta):
            raise ValueError("Document text metadata must be nonempty strings")
        if "fictional" in meta and not isinstance(meta["fictional"], bool):
            raise ValueError("fictional must be a boolean")
        rules = meta.get("rules", {})
        if not isinstance(rules, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in rules.items()):
            raise ValueError("rules must map rule identifiers to string values")
        effective = meta.get("effective_date")
        if effective is not None:
            effective = date.fromisoformat(effective).isoformat()
        document_id = meta.get("document_id", path.relative_to(directory).as_posix())
        if document_id in document_ids:
            raise ValueError("Duplicate document_id: maintain only the current file per document")
        document_ids.add(document_id)
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF requires an unlocked input document")
        extracted_pages = [page.extract_text() or "" for page in reader.pages]
        combined = " ".join(extracted_pages).lower()
        if "future founder challenge" in combined or "data-centric agents" in combined:
            raise ValueError("Event challenge PDFs are not a banking policy corpus")
        if not combined.strip():
            raise ValueError("PDF has no extractable text; OCR is required before ingestion")
        for page, text in enumerate(extracted_pages, 1):
            for position, passage in enumerate(chunk_text(text)):
                identity = f"{document_id}:{content_hash}:{page}:{position}"
                chunks.append({
                    "document_id": document_id, "title": meta.get("title", reader.metadata.title if reader.metadata and reader.metadata.title else path.stem),
                    "version": meta.get("version", content_hash[:12]), "effective_date": effective,
                    "content_hash": content_hash, "chunk_id": hashlib.sha256(identity.encode()).hexdigest(),
                    "page": page, "text": passage, "fictional": meta.get("fictional", False),
                    "applicability": meta.get("applicability"), "rules": rules,
                })
                if len(chunks) > MAX_CORPUS_CHUNKS:
                    raise ValueError("Corpus exceeds 20000 chunks; split or increase the reviewed limit")
    return chunks


def ingest(directory: Path, client=None, alias: str | None = None) -> dict:
    chunks = load_corpus(directory)
    digest = hashlib.sha256((INGESTOR_VERSION + json.dumps(chunks, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
    client = client if client is not None else get_client()
    alias = alias or index_name()
    active = client.indices.get_alias(name=alias) if client.indices.exists_alias(name=alias) else {}
    if active:
        mappings = client.indices.get_mapping(index=",".join(active))
        if len(mappings) == 1 and next(iter(mappings.values()))["mappings"].get("_meta", {}).get("corpus_hash") == digest:
            return {"status": "unchanged", "chunks": len(chunks), "corpus_hash": digest}
    elif client.indices.exists(index=alias):
        raise ValueError("OPENSEARCH_INDEX already names a physical index; choose a new alias")
    staging = f"{alias}-v-{digest[:12]}-{uuid4().hex[:8]}"
    client.indices.create(index=staging, body={
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {"_meta": {"corpus_hash": digest, "ingestor_version": INGESTOR_VERSION}, "dynamic": "strict", "properties": {
            "text": {"type": "text", "analyzer": "french"}, "title": {"type": "text", "analyzer": "french"},
            **{key: {"type": "keyword"} for key in ["document_id", "version", "content_hash", "chunk_id", "applicability"]},
            "page": {"type": "integer"}, "fictional": {"type": "boolean"},
            "effective_date": {"type": "date", "format": "strict_date"},
            "ingested_at": {"type": "date"}, "rules": {"type": "object", "enabled": False},
        }},
    })
    stamp = datetime.now(timezone.utc).isoformat()
    for offset in range(0, len(chunks), 250):
        operations = []
        for chunk in chunks[offset:offset + 250]:
            operations.extend([{"index": {"_index": staging, "_id": chunk["chunk_id"]}}, {**chunk, "ingested_at": stamp}])
        response = client.bulk(body=operations, refresh=False)
        if response.get("errors"):
            raise RuntimeError("OpenSearch rejected PDF chunks; active corpus unchanged")
    client.indices.refresh(index=staging)
    if client.count(index=staging)["count"] != len(chunks):
        raise RuntimeError("Chunk count verification failed; active corpus unchanged")
    actions = [{"remove": {"index": name, "alias": alias}} for name in active]
    actions.append({"add": {"index": staging, "alias": alias}})
    client.indices.update_aliases(body={"actions": actions})
    return {"status": "published", "chunks": len(chunks), "corpus_hash": digest, "index": staging}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=document_dir())
    args = parser.parse_args()
    try:
        print(json.dumps(ingest(args.directory)))
    except Exception as exc:
        # Never print client errors containing connection credentials.
        raise SystemExit(f"Ingestion failed ({type(exc).__name__}); active corpus unchanged. Check configuration and document metadata.") from None


if __name__ == "__main__":
    main()
