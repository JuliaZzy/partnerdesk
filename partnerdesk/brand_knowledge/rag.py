"""Brand RAG: chunk stored pages, embed, hybrid-search Milvus, optionally rerank, then measure.

Dense search is the embedding endpoint (`EMBEDDING_*`) plus a cosine index in Milvus.
Keyword search is BM25 over the same stored chunk text — the text already sits on the
vector row, so this does not add Elasticsearch. The two ranked lists are fused by
reciprocal rank; cosine and BM25 are not on one scale. Reranking, when `RERANK_BASE_URL`
is set, is the cross-encoder `BAAI/bge-reranker-v2-m3`: the question and one passage are
scored together. `evaluate_retrieval` scores a labeled run with `ranx` (MRR, nDCG,
Precision, Recall, MAP, hit rate). The chat model does none of this.

Indexing is explicit (`index_brand` / `python -m partnerdesk.brand_knowledge.rag <brand_id>`).
A question does not rebuild the index.
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from typing import Any

from ..config import embedding_settings, milvus_uri, rerank_settings
from .chunk import chunks_from_pages

TOP_K = 5
# Each retriever returns a wide list. Fusion, then rerank, cuts it to TOP_K.
RECALL_K = 20
RRF_K = 60
COLLECTION = "brand_knowledge"
_BATCH = 10
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# Milvus VARCHAR is measured in bytes. A paragraph chunk of CJK stays under this.
_TEXT_MAX = 8000


class RagUnavailable(RuntimeError):
    """Embedding endpoint or Milvus is not configured, or could not be reached."""


def source_label(name: str | None, page: Any, *, doc: str | None = None) -> str:
    """The file and page a citation points at. The number in front of it is assigned later."""
    title = (name or "").strip() or (doc or "").strip() or "document"
    try:
        n = int(page)
    except (TypeError, ValueError):
        return title
    return f"{title}，第{n}页"


def evidence_prompt(question: str, hits: list[dict[str, Any]]) -> str:
    """The user turn the existing text model reads. Retrieval already happened.

    Passages are numbered [1], [2], … so the answer can cite those numbers. `source` on a
    hit is the file-and-page label; without it the label is built from `doc` and `page`.
    """
    if hits:
        blocks = []
        for n, hit in enumerate(hits, start=1):
            label = str(hit.get("source") or source_label(None, hit.get("page"), doc=str(hit.get("doc") or "")))
            blocks.append(f"[{n}] {label}\n{hit.get('text') or ''}")
        context = "\n\n".join(blocks)
    else:
        context = "(no passages retrieved)"
    return f"Passages:\n{context}\n\nQuestion: {question}"


def _brand(brand_id: str) -> str:
    if not _ID.fullmatch(brand_id or ""):
        raise ValueError(f"brand id {brand_id!r} cannot be used in a Milvus filter")
    return brand_id


def embed(texts: list[str]) -> list[list[float]]:
    """Embed in batches of 10. The chat model is not this client."""
    settings = embedding_settings()
    if not settings.base_url:
        raise RagUnavailable(
            "EMBEDDING_BASE_URL is not set. The chat model does not embed text; "
            "point this at an OpenAI-compatible /embeddings endpoint."
        )
    if not settings.api_key:
        raise RagUnavailable("EMBEDDING_API_KEY is not set.")
    from openai import OpenAI

    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=60, max_retries=1)
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        resp = client.embeddings.create(
            model=settings.model, input=texts[i:i + _BATCH], dimensions=settings.dimensions,
        )
        out += [d.embedding for d in resp.data]
    return out


def _milvus():
    uri = milvus_uri()
    if not uri:
        raise RagUnavailable("MILVUS_URI is not set.")
    from pymilvus import MilvusClient

    try:
        return MilvusClient(uri)
    except Exception as err:
        raise RagUnavailable(f"could not reach Milvus at {uri}") from err


def _ensure(client, dimension: int) -> None:
    if client.has_collection(COLLECTION):
        return
    from pymilvus import DataType

    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dimension)
    schema.add_field("brand_id", DataType.VARCHAR, max_length=64)
    schema.add_field("cid", DataType.VARCHAR, max_length=160)
    schema.add_field("doc", DataType.VARCHAR, max_length=64)
    schema.add_field("page", DataType.INT64)
    schema.add_field("text", DataType.VARCHAR, max_length=_TEXT_MAX)
    index = client.prepare_index_params()
    index.add_index(field_name="vector", metric_type="COSINE")
    client.create_collection(collection_name=COLLECTION, schema=schema, index_params=index)


def index_brand(db, brand_id: str) -> int:
    """Replace this brand's vectors with chunks of its stored pages. Other brands stay."""
    brand_id = _brand(brand_id)
    chunks: list[dict[str, Any]] = []
    for doc in db.documents(brand_id):
        chunks.extend(chunks_from_pages(doc.id, db.document_pages(doc.id)))
    if not chunks:
        return 0
    vectors = embed([c["text"] for c in chunks])
    client = _milvus()
    _ensure(client, len(vectors[0]))
    if client.has_collection(COLLECTION):
        client.delete(collection_name=COLLECTION, filter=f'brand_id == "{brand_id}"')
    client.insert(COLLECTION, [
        {
            "vector": vector, "brand_id": brand_id, "cid": c["cid"], "doc": c["doc"],
            "page": int(c["page"]), "text": c["text"][:_TEXT_MAX],
        }
        for c, vector in zip(chunks, vectors, strict=True)
    ])
    client.flush(COLLECTION)
    return len(chunks)


_RUN = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*|[\u4e00-\u9fff]+")
_QUERY_PAGE = 500


def tokenize(text: str) -> list[str]:
    """Tokens for BM25. An ASCII word or measure stays whole; Han text keeps characters and bigrams.

    ``冷萃 500ml`` is ``冷``, ``萃``, ``冷萃``, ``500ml``. A character still matches, and the bigram
    is the specific term. Splitting ``500ml`` into digits would match any page that uses those digits.
    """
    tokens: list[str] = []
    for match in _RUN.finditer(text or ""):
        piece = match.group()
        if piece[0].isascii():
            tokens.append(piece.lower())
            continue
        tokens.extend(piece)
        if len(piece) > 1:
            tokens.extend(piece[i:i + 2] for i in range(len(piece) - 1))
    return tokens


def bm25_search(query: str, chunks: list[dict[str, Any]], *, limit: int = RECALL_K) -> list[dict[str, Any]]:
    """BM25 over one brand's chunks. Scores below zero are omitted. Lucene's smoothed IDF."""
    terms = list(dict.fromkeys(tokenize(query)))
    if not terms or not chunks:
        return []
    docs = [(chunk, tokenize(str(chunk.get("text") or ""))) for chunk in chunks]
    lengths = [len(tokens) for _, tokens in docs]
    avgdl = sum(lengths) / len(docs)
    if avgdl == 0:
        return []
    df: dict[str, int] = {}
    for _, tokens in docs:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    n = len(docs)
    k1, b = 1.5, 0.75
    ranked: list[tuple[float, dict[str, Any]]] = []
    for chunk, tokens in docs:
        if not tokens:
            continue
        tf: dict[str, int] = {}
        for term in tokens:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        dl = len(tokens)
        for term in terms:
            freq = tf.get(term)
            if not freq:
                continue
            n_q = df.get(term, 0)
            idf = math.log((n - n_q + 0.5) / (n_q + 0.5) + 1)
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * dl / avgdl))
        if score > 0:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [{**chunk, "score": score} for score, chunk in ranked[:limit]]


def reciprocal_rank_fusion(
    rankings: list[list[dict[str, Any]]], *, limit: int = RECALL_K,
) -> list[dict[str, Any]]:
    """Merge ranked lists by place, not by raw score. `k` is the usual RRF constant."""
    scores: dict[str, float] = {}
    chosen: dict[str, dict[str, Any]] = {}
    for ranking in rankings:
        for place, hit in enumerate(ranking):
            cid = str(hit.get("cid") or "")
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + place + 1)
            chosen.setdefault(cid, hit)
    ordered = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [{**chosen[cid], "score": scores[cid]} for cid in ordered[:limit]]


def rerank(question: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rescore fused hits with the cross-encoder (`RERANK_MODEL`, default `BAAI/bge-reranker-v2-m3`).

    No `RERANK_BASE_URL` leaves the fused order.
    """
    settings = rerank_settings()
    if not settings.base_url or not hits:
        return hits[:TOP_K]
    if not settings.api_key:
        raise RagUnavailable("RERANK_API_KEY is not set.")
    pool = hits[:RECALL_K]
    url = settings.base_url.rstrip("/")
    if not url.endswith("/rerank"):
        url += "/rerank"
    body = json.dumps({
        "model": settings.model,
        "query": question,
        "documents": [str(hit.get("text") or "") for hit in pool],
        "top_n": min(TOP_K, len(pool)),
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:300]
        raise RagUnavailable(f"rerank HTTP {err.code}: {detail}") from err
    except urllib.error.URLError as err:
        raise RagUnavailable(f"rerank request failed: {err.reason}") from err
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        raise RagUnavailable("rerank returned no scores.")
    ordered: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or not 0 <= index < len(pool):
            continue
        score = item.get("relevance_score", item.get("score"))
        ordered.append({**pool[index], "score": score})
    if not ordered:
        raise RagUnavailable("rerank returned no scores.")
    return ordered[:TOP_K]


def _brand_chunks(client, brand_id: str) -> list[dict[str, Any]]:
    """Every stored chunk for this brand. BM25 has to see the pages dense search missed."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    while True:
        batch = client.query(
            COLLECTION, filter=f'brand_id == "{brand_id}"',
            output_fields=["cid", "doc", "page", "text"], limit=_QUERY_PAGE, offset=offset,
        )
        fresh = [row for row in batch if row.get("cid") not in seen]
        for row in fresh:
            seen.add(row["cid"])
        rows.extend(fresh)
        if len(batch) < _QUERY_PAGE or not fresh:
            return rows
        offset += _QUERY_PAGE


def _dense_search(client, brand_id: str, question: str) -> list[dict[str, Any]]:
    vector = embed([question])[0]
    res = client.search(
        COLLECTION, data=[vector], limit=RECALL_K,
        filter=f'brand_id == "{brand_id}"',
        output_fields=["cid", "doc", "page", "text"],
    )
    hits = []
    for hit in res[0]:
        entity = hit.get("entity") or {}
        hits.append({
            "cid": entity.get("cid"), "doc": entity.get("doc"), "page": entity.get("page"),
            "text": entity.get("text") or "", "score": hit.get("distance"),
        })
    return hits


def _grades(qid: str, relevant: Any) -> dict[str, int]:
    if isinstance(relevant, dict):
        grades = {str(cid): int(grade) for cid, grade in relevant.items() if str(cid)}
    elif isinstance(relevant, (list, tuple, set, frozenset)):
        grades = {str(cid): 1 for cid in relevant if str(cid)}
    else:
        raise TypeError(f"query {qid!r} relevance must be grades or chunk ids")
    if not any(grade > 0 for grade in grades.values()):
        raise ValueError(f"query {qid!r} has no relevant chunk")
    return grades


def _ranked(hits: list[dict[str, Any]] | None) -> dict[str, float]:
    """cid → score, highest first. A hit with no score keeps the list order for the whole query."""
    rows = [hit for hit in (hits or []) if isinstance(hit, dict) and str(hit.get("cid") or "")]
    numbers: list[float | None] = []
    for hit in rows:
        score = hit.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            numbers.append(None)
        else:
            numbers.append(float(score))
    use_scores = bool(rows) and all(score is not None for score in numbers)
    out: dict[str, float] = {}
    for place, (hit, score) in enumerate(zip(rows, numbers, strict=True)):
        cid = str(hit["cid"])
        if cid in out:
            continue
        out[cid] = float(score) if use_scores else 1.0 / (place + 1)
    return out


def evaluate_retrieval(
    qrels: dict[str, Any],
    run: dict[str, list[dict[str, Any]]],
    *,
    k: int = TOP_K,
) -> dict[str, float]:
    """Mean scores for a labeled run. `ranx` computes them.

    `qrels` maps a query id to relevance grades (`{cid: grade}`) or to relevant chunk ids
    (each grade 1). A grade above 0 is relevant. nDCG uses the grade as gain ``2**grade - 1``,
    discounted by ``log2(rank + 1)``. `run` maps the same id to hits in the shape `retrieve`
    returns. A higher `score` ranks first. If any hit has no score, that query is ranked by
    list order. A labeled query missing from `run` scores 0.

    MRR, nDCG@k, Precision@k and Recall@k are the ranking measures. MAP is reported with
    precision, and Hit Rate@k is whether any relevant chunk is in the top k. MRR and MAP
    use the whole submitted ranking; the others cut at `k` (default `TOP_K`).
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    if not qrels:
        raise ValueError("no labeled queries")
    from ranx import Qrels, Run, evaluate

    judged = {str(qid): _grades(str(qid), relevant) for qid, relevant in qrels.items()}
    ranked = {qid: _ranked(run.get(qid)) for qid in judged}
    populated = {qid: scores for qid, scores in ranked.items() if scores}
    # `ndcg_burges` is the graded form trec_eval reports. Binary grades match linear nDCG.
    specs = [
        ("mrr", "mrr"),
        (f"ndcg@{k}", f"ndcg_burges@{k}"),
        (f"precision@{k}", f"precision@{k}"),
        (f"recall@{k}", f"recall@{k}"),
        ("map", "map"),
        (f"hit_rate@{k}", f"hit_rate@{k}"),
    ]
    raw = evaluate(
        Qrels(judged),
        Run(populated) if populated else Run(),
        [spec for _, spec in specs],
        make_comparable=True,
        save_results_in_run=False,
    )
    return {name: float(raw[spec]) for name, spec in specs}


def retrieve(question: str, brand_id: str) -> list[dict[str, Any]]:
    """Hybrid search for one brand: dense recall, BM25 recall, rank fusion, optional rerank."""
    brand_id = _brand(brand_id)
    question = (question or "").strip()
    if not question:
        return []
    client = _milvus()
    if not client.has_collection(COLLECTION):
        return []
    corpus = _brand_chunks(client, brand_id)
    if not corpus:
        return []
    dense = _dense_search(client, brand_id, question)
    sparse = bm25_search(question, corpus)
    return rerank(question, reciprocal_rank_fusion([dense, sparse]))


def main(argv: list[str] | None = None) -> None:
    import argparse

    from ..db import Database

    parser = argparse.ArgumentParser(description="Embed one brand's document pages into Milvus.")
    parser.add_argument("brand_id")
    args = parser.parse_args(argv)
    print(f"indexed {index_brand(Database(), args.brand_id)} chunks for {args.brand_id}")


if __name__ == "__main__":
    main()
