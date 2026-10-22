"""Règles internes de la banque : articles des PDF indexés dans OpenSearch (Aiven)."""
import os

from opensearchpy import OpenSearch

OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "")
OPENSEARCH_INDEX = os.environ.get("OPENSEARCH_INDEX", "rules-current")
_FIELDS = ["article_id", "doc_code", "doc_title", "file", "version", "section", "title", "text", "refs"]
_client: OpenSearch | None = None


def client() -> OpenSearch:
    global _client
    if _client is None:
        if not OPENSEARCH_URL:
            raise RuntimeError("OPENSEARCH_URL missing")
        # L'URL Aiven embarque les identifiants (https://user:pass@host:port)
        _client = OpenSearch(OPENSEARCH_URL, verify_certs=True, timeout=10)
    return _client


def _clause(hit: dict) -> dict:
    s = hit["_source"]
    return {**s, "score": hit.get("_score"), "source": f"{s['doc_title']} §{s['article_id']}"}


def search(question: str, k: int = 5, doc_code: str | None = None) -> list[dict]:
    """Recherche plein texte (BM25) sur le titre, la section et le texte des articles."""
    must = {"multi_match": {"query": question, "fields": ["title^2", "section", "text", "doc_title"]}}
    body = {
        "size": k,
        "_source": _FIELDS,
        "query": {"bool": {"must": must, "filter": [{"term": {"doc_code": doc_code.upper()}}] if doc_code else []}},
    }
    res = client().search(index=OPENSEARCH_INDEX, body=body)
    return [_clause(h) for h in res["hits"]["hits"]]


def get_articles(article_ids: list[str]) -> list[dict]:
    """Articles par identifiant (ex. CARD-2.1), pour suivre les renvois (refs)."""
    body = {"size": len(article_ids), "_source": _FIELDS, "query": {"terms": {"article_id": article_ids}}}
    res = client().search(index=OPENSEARCH_INDEX, body=body)
    return [_clause(h) for h in res["hits"]["hits"]]
