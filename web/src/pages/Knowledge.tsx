import { useEffect, useState } from "react";

import { api, postForm, postJson } from "../api";
import { NeedPartnership, useDesk } from "../desk";
import { shortDate } from "../format";
import { Badge, Metrics, PageHead, Tabs } from "../ui";

type Doc = {
  id: string;
  title: string | null;
  file_name: string;
  status: string;
  page_count: number | null;
  created_at: string | null;
  distilled_at: string | null;
  model: string | null;
  summary: string | null;
  sections: { type: string }[];
};

type Fragment = {
  id: string;
  document_id: string | null;
  product_id: string | null;
  product_name: string | null;
  type: string;
  title: string;
  content: string;
  status: string;
  tags: string[];
  source_pages: number[];
};

export function Knowledge() {
  return <NeedPartnership><KnowledgeDesk /></NeedPartnership>;
}

function KnowledgeDesk() {
  const { partnership, meta } = useDesk();
  const p = partnership!;
  const [docs, setDocs] = useState<Doc[]>([]);
  const [fragments, setFragments] = useState<Fragment[]>([]);
  const [tab, setTab] = useState("Documents");
  const [status, setStatus] = useState("all");
  const [types, setTypes] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [flash, setFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pages, setPages] = useState<Record<string, { n: number; text: string }[]>>({});
  const [draft, setDraft] = useState({ type: "product", title: "", content: "", product: "", tags: "" });

  async function load() {
    const [nextDocs, nextFragments] = await Promise.all([
      api<Doc[]>(`/api/brands/${p.brand_id}/documents`),
      api<Fragment[]>(`/api/brands/${p.brand_id}/fragments`),
    ]);
    setDocs(nextDocs);
    setFragments(nextFragments);
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.brand_id]);

  const modelLabel = (id: string | null) => meta?.models.find((item) => item.id === id)?.label || id || "—";
  const shown = fragments.filter((frag) => (
    (status === "all" || frag.status === status)
    && (types.length === 0 || types.includes(frag.type))
    && (!query || `${frag.title} ${frag.content} ${frag.tags.join(" ")} ${frag.product_name || ""}`.toLowerCase().includes(query.toLowerCase()))
  ));
  const typeOptions = [...new Set(fragments.map((frag) => frag.type))].sort();

  async function upload() {
    if (!file) return;
    setBusy("upload");
    setError(null);
    try {
      const doc = await postForm<Doc>(`/api/brands/${p.brand_id}/documents`, file, { title });
      setFlash(`Stored ${doc.title}: ${doc.page_count ?? 0} page(s)`);
      setFile(null);
      setTitle("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      setBusy(null);
    }
  }

  async function distil(doc: Doc) {
    setBusy(doc.id);
    setError(null);
    try {
      const model = localStorage.getItem("partnerdesk.model") || meta?.default_model || "";
      const result = await api<{ sections: number; fragments: number; pages_processed: number; skipped: string[] }>(
        `/api/documents/${doc.id}/distill?model=${encodeURIComponent(model)}`,
        { method: "POST" },
      );
      setFlash(`${doc.title || doc.file_name}: ${result.sections} kind(s) of content, ${result.fragments} fragment(s) from ${result.pages_processed} page(s) — review them under Fragments.${result.skipped.length ? ` Skipped: ${result.skipped.join("; ")}` : ""}`);
      setTab("Fragments");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "distillation failed");
    } finally {
      setBusy(null);
    }
  }

  async function showPages(id: string) {
    if (pages[id]) return;
    const body = await api<{ pages: { n: number; text: string }[] }>(`/api/documents/${id}`);
    setPages((current) => ({ ...current, [id]: body.pages }));
  }

  async function setStatusOf(id: string, next: string) {
    await postJson(`/api/fragments/${id}/status`, { status: next });
    await load();
  }

  return (
    <>
      <PageHead title="Brand knowledge" caption={`${p.brand_name} — what the agent is allowed to say about the brand and its products, with the page it came from.`} />
      {error && <div className="error">{error}</div>}
      {flash && <div className="card ok-note">{flash}</div>}
      <Metrics items={[
        { label: "Documents", value: String(docs.length) },
        { label: "Fragments", value: String(fragments.length) },
        { label: "Approved", value: String(fragments.filter((frag) => frag.status === "approved").length) },
        { label: "Awaiting review", value: String(fragments.filter((frag) => frag.status === "draft").length) },
      ]} />
      <Tabs labels={["Documents", "Fragments"]} value={tab} onChange={setTab} />
      {tab === "Documents" && (
        <>
          <details className="card" open={docs.length === 0}>
            <summary>Add material</summary>
            <p className="muted">PDF, Word, Markdown or plain text. Distilling is a separate step because it costs model calls. A vision model also sees each PDF page.</p>
            <div className="filters">
              <label className="field grow">Document
                <input type="file" accept=".pdf,.docx,.md,.txt" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
              </label>
              <label className="field grow">Title
                <input value={title} placeholder={file?.name || "Spring 2026 product deck"} onChange={(event) => setTitle(event.target.value)} />
              </label>
              <button className="btn-primary" type="button" disabled={!file || busy === "upload"} onClick={() => void upload()}>Upload</button>
            </div>
          </details>
          {docs.length === 0 && <div className="empty">No material yet. Upload a deck, a catalogue or a brand book.</div>}
          {docs.map((doc) => {
            const frags = fragments.filter((frag) => frag.document_id === doc.id);
            return (
              <article className="card" key={doc.id}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <div>
                    <strong>{doc.title || doc.file_name}</strong> <Badge status={doc.status} />
                    <div className="muted">
                      {doc.file_name} · {doc.page_count ?? 0} page(s) · uploaded {shortDate(doc.created_at)}
                      {doc.distilled_at ? ` · distilled ${shortDate(doc.distilled_at)} with ${modelLabel(doc.model)}` : ""} · {frags.length} fragment(s)
                    </div>
                    {doc.summary && <p className="muted">{doc.summary}</p>}
                    <div>{doc.sections.map((section) => <span className="badge tag" key={section.type} style={{ marginRight: 6 }}>{section.type}</span>)}</div>
                  </div>
                  <div className="actions">
                    <button className="btn" type="button" disabled={busy === doc.id} onClick={() => void distil(doc)}>
                      {busy === doc.id ? "Distilling…" : doc.status === "distilled" ? "Distil again" : "Distil"}
                    </button>
                    <button className="btn" type="button" onClick={() => void api(`/api/documents/${doc.id}`, { method: "DELETE" }).then(load)}>Delete</button>
                  </div>
                </div>
                <details onToggle={(event) => { if ((event.target as HTMLDetailsElement).open) void showPages(doc.id); }}>
                  <summary>Pages</summary>
                  {(pages[doc.id] ?? []).map((page) => (
                    <div key={page.n}>
                      <strong>Page {page.n}</strong>
                      <pre>{page.text.slice(0, 1500)}{page.text.length > 1500 ? "…" : ""}</pre>
                    </div>
                  ))}
                </details>
              </article>
            );
          })}
        </>
      )}
      {tab === "Fragments" && (
        <>
          <div className="filters">
            <label className="field">Status
              <select value={status} onChange={(event) => setStatus(event.target.value)}>
                {["all", "draft", "approved", "archived"].map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <div className="field">
              <span>Type</span>
              <div className="actions">
                {typeOptions.map((item) => (
                  <button key={item} type="button" className={types.includes(item) ? "btn-primary" : "btn"} onClick={() => setTypes((current) => current.includes(item) ? current.filter((t) => t !== item) : [...current, item])}>{item}</button>
                ))}
              </div>
            </div>
            <label className="field grow">Search
              <input value={query} placeholder="Search title, content, tags" onChange={(event) => setQuery(event.target.value)} />
            </label>
          </div>
          <p className="muted">{shown.length} of {fragments.length} fragment(s)</p>
          {shown.map((frag) => {
            const src = docs.find((doc) => doc.id === frag.document_id);
            return (
              <article className="card" key={frag.id}>
                <strong>{frag.title}</strong> <span className="badge tag">{frag.type}</span> <Badge status={frag.status} />
                {frag.product_id && <span className="badge ok">{frag.product_name || frag.product_id}</span>}
                {!frag.product_id && frag.product_name && <span className="badge mute">{frag.product_name} — not in catalog</span>}
                <p>{frag.content}</p>
                <p className="muted">{[src?.title || src?.file_name, frag.source_pages.length ? `pages ${frag.source_pages.join(", ")}` : null, frag.tags.map((tag) => `#${tag}`).join(", ")].filter(Boolean).join(" · ")}</p>
                <div className="actions">
                  {frag.status !== "approved" && <button className="btn-primary" type="button" onClick={() => void setStatusOf(frag.id, "approved")}>Approve</button>}
                  {frag.status !== "archived" && <button className="btn" type="button" onClick={() => void setStatusOf(frag.id, "archived")}>Archive</button>}
                  <button className="btn" type="button" onClick={() => void api(`/api/fragments/${frag.id}`, { method: "DELETE" }).then(load)}>Delete</button>
                </div>
              </article>
            );
          })}
          <details className="card">
            <summary>Add a fragment by hand</summary>
            <form className="filters" onSubmit={(event) => {
              event.preventDefault();
              if (!draft.title.trim() || !draft.content.trim()) { setError("A title and content are needed."); return; }
              void postJson(`/api/brands/${p.brand_id}/fragments`, {
                type: draft.type, title: draft.title, content: draft.content,
                product_name: draft.product || null,
                tags: draft.tags.split(",").map((tag) => tag.trim()).filter(Boolean),
              }).then(() => { setDraft({ type: "product", title: "", content: "", product: "", tags: "" }); return load(); })
                .catch((err: Error) => setError(err.message));
            }}>
              <label className="field">Type
                <select value={draft.type} onChange={(event) => setDraft({ ...draft, type: event.target.value })}>
                  {Object.entries(meta?.fragment_types ?? {}).map(([key, desc]) => <option key={key} value={key}>{key} — {desc}</option>)}
                </select>
              </label>
              <label className="field grow">Title
                <input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
              </label>
              <label className="field grow">Content
                <input value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} />
              </label>
              <label className="field">Product
                <input value={draft.product} placeholder="Name or SKU" onChange={(event) => setDraft({ ...draft, product: event.target.value })} />
              </label>
              <label className="field">Tags
                <input value={draft.tags} placeholder="comma-separated" onChange={(event) => setDraft({ ...draft, tags: event.target.value })} />
              </label>
              <button className="btn-primary" type="submit">Add</button>
            </form>
          </details>
        </>
      )}
    </>
  );
}
