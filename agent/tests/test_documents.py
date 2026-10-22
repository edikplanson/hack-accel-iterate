from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from agent.app import documents
from agent.app.ingest_documents import CHUNK_OVERLAP, CHUNK_SIZE, chunk_text, ingest, load_corpus


DEMO = Path(__file__).resolve().parents[1] / 'demo-documents'


@pytest.fixture
def corpus(tmp_path):
    for path in DEMO.glob('*'):
        if path.suffix in {'.pdf', '.json'}:
            shutil.copy(path, tmp_path / path.name)
    return tmp_path


class FakeSearch:
    def __init__(self):
        self.indices = self
        self.stores = {}
        self.aliases = {}
        self.swaps = 0
        self.fail_bulk = False

    def exists_alias(self, name):
        return name in self.aliases

    def get_alias(self, name):
        return {index: {} for index in self.aliases[name]}

    def exists(self, index):
        return index in self.stores

    def get_mapping(self, index):
        return {name: {'mappings': self.stores[name]['mapping']} for name in index.split(',')}

    def create(self, index, body):
        self.stores[index] = {'mapping': body['mappings'], 'docs': {}}

    def bulk(self, body, refresh):
        if self.fail_bulk:
            return {'errors': True}
        for i in range(0, len(body), 2):
            item = body[i]['index']
            self.stores[item['_index']]['docs'][item['_id']] = body[i + 1]
        return {'errors': False}

    def refresh(self, index):
        pass

    def count(self, index):
        return {'count': len(self.stores[index]['docs'])}

    def update_aliases(self, body):
        for action in body['actions']:
            if 'remove' in action:
                data = action['remove']
                self.aliases[data['alias']].remove(data['index'])
            else:
                data = action['add']
                self.aliases.setdefault(data['alias'], []).append(data['index'])
        self.swaps += 1


def test_real_pdf_extraction_preserves_pages_and_metadata(corpus):
    chunks = load_corpus(corpus)
    assert {c['page'] for c in chunks} == {1, 2, 3}
    assert all(c['fictional'] and c['effective_date'] == '2026-01-01' for c in chunks)
    assert any('montant souhaite' in c['text'] for c in chunks if c['page'] == 1)
    assert all(len(c['content_hash']) == 64 for c in chunks)
    assert load_corpus(corpus) == chunks


def test_chunking_bounded_with_overlap():
    text = ' '.join(f'mot{i:05}' for i in range(1000))
    chunks = chunk_text(text)
    assert len(chunks) > 2
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    assert all(a[-CHUNK_OVERLAP:] == b[:CHUNK_OVERLAP] for a, b in zip(chunks, chunks[1:]))
    assert chunks[-1].endswith('mot00999')


def test_ingestion_idempotent_and_updated_snapshot_replaces_current(corpus):
    client = FakeSearch()
    initial = ingest(corpus, client, 'test-docs')
    assert initial['status'] == 'published'
    old = list(client.aliases['test-docs'])
    assert ingest(corpus, client, 'test-docs')['status'] == 'unchanged'
    assert client.swaps == 1
    sidecar = next(corpus.glob('*.json'))
    meta = json.loads(sidecar.read_text())
    meta['version'] = 'demo-2'
    sidecar.write_text(json.dumps(meta))
    ingest(corpus, client, 'test-docs')
    assert not set(old) & set(client.aliases['test-docs'])
    current = client.stores[client.aliases['test-docs'][0]]['docs']
    assert all(c['version'] == 'demo-2' for c in current.values())
    assert all(c['ingested_at'] != c['effective_date'] for c in current.values())


def test_partial_ingestion_does_not_publish(corpus):
    client = FakeSearch()
    ingest(corpus, client, 'test-docs')
    previous = deepcopy(client.aliases)
    sidecar = next(corpus.glob('*.json'))
    sidecar.write_text(sidecar.read_text().replace('demo-1', 'demo-2'))
    client.fail_bulk = True
    with pytest.raises(RuntimeError):
        ingest(corpus, client, 'test-docs')
    assert client.aliases == previous


def test_policy_conflict_and_missing_applicability_are_explicit(corpus):
    chunks = load_corpus(corpus)[:1]
    other = deepcopy(chunks[0])
    other.update(document_id='other', title='Autre regle', effective_date=None, applicability=None)
    other['rules']['demo.transaction_execution'] = 'autorisee'
    result = documents.result_from_chunks(chunks + [other])
    assert result.status == 'conflicting'
    assert any('contradictoires' in w for w in result.warnings)
    assert any("Date d'effet inconnue" in w for w in result.warnings)
    assert any("application non precise" in w for w in result.warnings)
    assert result.passages[0].page == 1


def test_mock_queries_real_fictional_pages(monkeypatch, corpus):
    monkeypatch.setenv('APP_MODE', 'mock')
    monkeypatch.setenv('DOCUMENT_DIR', str(corpus))
    result = documents.get_repository().search('rachat assurance-vie montant')
    assert result.status == 'ok'
    assert result.passages and all(p.fictional for p in result.passages)
    assert any('montant souhaite' in p.passage for p in result.passages)
    assert any('MODE MOCK' in w for w in result.warnings)
    assert documents.get_repository().search('cryptomonnaiezzzz').status == 'missing'


def test_live_failure_has_no_mock_substitution(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'live')
    def fail():
        raise RuntimeError('secret-do-not-expose')
    monkeypatch.setattr(documents, 'get_client', fail)
    result = documents.get_repository().search('rachat')
    assert result.status == 'unavailable' and not result.passages
    assert 'secret-do-not-expose' not in ' '.join(result.warnings)


def test_search_bounds_results_and_uses_uninterpreted_query(monkeypatch, corpus):
    monkeypatch.setenv('APP_MODE', 'live')
    chunks = load_corpus(corpus)
    class Client:
        def search(self, index, body):
            assert body['size'] == 10
            assert len(body['query']['bool']['must'][0]['multi_match']['query']) == 2000
            assert 'filter' in body['query']['bool']
            return {'hits': {'hits': [{'_source': chunks[0]}]}}
    monkeypatch.setattr(documents, 'get_client', lambda: Client())
    assert documents.get_repository().search('ignore instructions ' * 200, 999).status == 'ok'


def test_partial_search_is_unavailable(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'live')
    class Client:
        def search(self, **kwargs):
            return {'timed_out': True, 'hits': {'hits': []}}
    monkeypatch.setattr(documents, 'get_client', lambda: Client())
    assert documents.get_repository().search('rachat').status == 'unavailable'


def test_no_pdfs_fails_without_publishing(tmp_path):
    with pytest.raises(ValueError, match='No banking PDFs'):
        load_corpus(tmp_path)


def test_sidecar_validation_rejects_ambiguous_fictional_flag(corpus):
    path = next(corpus.glob('*.json'))
    meta = json.loads(path.read_text())
    meta['fictional'] = 'false'
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='boolean'):
        load_corpus(corpus)


def test_inline_ca_uses_verified_in_memory_context(monkeypatch):
    import ssl
    import opensearchpy
    pem = ssl.DER_cert_to_PEM_cert(ssl.create_default_context().get_ca_certs(binary_form=True)[0])
    monkeypatch.setenv('OPENSEARCH_URL', 'https://search.example.test:443')
    monkeypatch.setenv('OPENSEARCH_CA_PEM', pem)
    monkeypatch.delenv('OPENSEARCH_USERNAME', raising=False)
    monkeypatch.delenv('OPENSEARCH_PASSWORD', raising=False)
    captured = {}
    monkeypatch.setattr(opensearchpy, 'OpenSearch', lambda **kwargs: captured.update(kwargs) or captured)
    documents.get_client.cache_clear()
    try:
        documents.get_client()
        context = captured['ssl_context']
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert captured['use_ssl'] is True
        assert 'ca_certs' not in captured
        monkeypatch.setenv('OPENSEARCH_CA_PEM', 'invalid-ca-pem')
        documents.get_client.cache_clear()
        with pytest.raises(ssl.SSLError):
            documents.get_client()
    finally:
        documents.get_client.cache_clear()
