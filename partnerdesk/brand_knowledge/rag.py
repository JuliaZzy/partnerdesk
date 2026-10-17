"""Minimal brand RAG: chunk stored pages, embed, search Milvus, answer from those passages.

Same shape as a one-collection demo (chunk → embed → cosine search → cite). Two project
seams replace the demo's hardcoded clients:

- embeddings are their own OpenAI-compatible endpoint (`EMBEDDING_*`), not the chat model
- the chat model is the one the desk already holds; this module only retrieves

Indexing is explicit (`index_brand` / `python -m partnerdesk.brand_knowledge.rag <brand_id>`).
A question does not rebuild the index.
"""

from __future__ import annotations

import re
from typing import Any

from ..config import embedding_settings, milvus_uri

CHUNK_CHARS = 350
TOP_K = 5
COLLECTION = "brand_knowledge"
_BATCH = 10
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# Milvus VARCHAR is measured in bytes; a 350-char chunk of CJK is well under this.
_TEXT_MAX = 8000


class RagUnavailable(RuntimeError):
    """Embedding endpoint or Milvus is not configured, or could not be reached."""


def chunks_from_pages(doc_id: str, pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """One document's pages → ~350-character chunks. A chunk remembers the page it started on.

    A page is already split out of the file, so there is no `<!-- page N -->` marker to scan.
    A single paragraph longer than the chunk size is sliced; otherwise one oversized page
    would not fit the vector field."""
    chunks: list[dict[str, Any]] = []
    for page, text in pages:
        buf = ""
        for para in (text or "").split("\n\n"):
            para = para.strip()
            if not para or para.startswith("# "):
                continue
            for piece in _slices(para):
                if buf and len(buf) + len(piece) > CHUNK_CHARS:
                    chunks.append({"doc": doc_id, "page": page, "text": buf})
                    buf = ""
                buf = (buf + "\n" + piece).strip()
        if buf:
            chunks.append({"doc": doc_id, "page": page, "text": buf})
    for i, c in enumerate(chunks):
        c["cid"] = f"{c['doc']}#p{c['page']}-{i}"
    return chunks


def _slices(para: str) -> list[str]:
    if len(para) <= CHUNK_CHARS:
        return [para]
    return [para[i:i + CHUNK_CHARS] for i in range(0, len(para), CHUNK_CHARS)]


def evidence_prompt(question: str, hits: list[dict[str, Any]]) -> str:
    """The user turn the existing text model reads. Retrieval already happened."""
    if hits:
        context = "\n\n".join(
            f"[{h['cid']}] ({h['doc']} p.{h['page']})\n{h['text']}" for h in hits
        )
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


def retrieve(question: str, brand_id: str) -> list[dict[str, Any]]:
    """Embed the question as given and return the nearest chunks for this brand."""
    brand_id = _brand(brand_id)
    question = (question or "").strip()
    if not question:
        return []
    client = _milvus()
    if not client.has_collection(COLLECTION):
        return []
    vector = embed([question])[0]
    res = client.search(
        COLLECTION, data=[vector], limit=TOP_K,
        filter=f'brand_id == "{brand_id}"',
        output_fields=["cid", "doc", "page", "text"],
    )
    return [{"score": hit["distance"], **hit["entity"]} for hit in res[0]]


def main(argv: list[str] | None = None) -> None:
    import argparse

    from ..db import Database

    parser = argparse.ArgumentParser(description="Embed one brand's document pages into Milvus.")
    parser.add_argument("brand_id")
    args = parser.parse_args(argv)
    print(f"indexed {index_brand(Database(), args.brand_id)} chunks for {args.brand_id}")


if __name__ == "__main__":
    main()
