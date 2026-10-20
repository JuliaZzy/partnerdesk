import { useEffect, useRef, useState } from "react";

import { api, postJson, streamMessage } from "../api";
import { NeedPartnership, useDesk } from "../desk";
import {
  applyActivity,
  buildLedger,
  citationSources,
  confirmReady,
  dataQueryMark,
  discountText,
  greeting,
  lineFlag,
  money,
  reportCharts,
  webNotes,
  type Activity,
  type DataQueryMark,
  type DraftLine,
  type ReportChart,
  type Step,
} from "../format";
import { ReportFigure } from "../ui";

type Turn = {
  role: "user" | "assistant";
  text: string;
  images?: string[];
  files?: string[];
  model?: string;
  warnings?: string[];
  sources?: { n: number; source: string }[];
  dataQuery?: DataQueryMark;
  charts?: ReportChart[];
  webNotes?: string[];
  reportDraft?: boolean;
  activity?: Activity[];
};

type Gap = { message: string; candidates?: { name: string; sku: string }[] };

type Draft = {
  currency: string;
  lines: DraftLine[];
  payment_terms: string;
  incoterms: string;
  shipping_method: string;
  eta_date: string | null;
  discount: { kind: string; value?: number | null; source?: string | null } | null;
  notes?: string | null;
  total: number | null;
};

type Snap = {
  turns: Turn[];
  draft: Draft | null;
  gaps: Gap[];
  steps: Step[];
  stage: string;
  confirmToken: string | null;
  route: string;
  title: string | null;
  tags: string[];
};

type SessionRow = {
  id: string;
  stage?: string;
  title?: string | null;
  tags?: string[];
  confirm_token?: string | null;
  history?: {
    role: string;
    content?: string;
    sources?: { n: number; source: string }[];
    data_query?: unknown;
    charts?: unknown;
    web_notes?: unknown;
    report_draft?: boolean;
  }[];
};

const snaps = new Map<string, Snap>();
const IMAGE = new Set(["png", "jpg", "jpeg", "webp"]);
const DOC = new Set(["pdf", "docx", "txt", "md"]);
const SKIP = new Set(["(message)", "(confirm)"]);
const STEP_MARK: Record<string, string> = { done: "✓", working: "…", "needs-you": "!", pending: "·" };

function readDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function fromSession(session: SessionRow, hello: string): Snap {
  const turns: Turn[] = [{ role: "assistant", text: hello }];
  for (const message of session.history ?? []) {
    const text = String(message.content ?? "");
    if ((message.role === "user" || message.role === "assistant") && !SKIP.has(text)) {
      const sources = message.role === "assistant" ? citationSources(message.sources) : undefined;
      const dataQuery = message.role === "assistant" ? dataQueryMark(message.data_query) : undefined;
      const charts = message.role === "assistant" ? reportCharts(message.charts) : undefined;
      const notes = message.role === "assistant" ? webNotes(message.web_notes) : undefined;
      const reportDraft = message.role === "assistant" && message.report_draft === true;
      turns.push({ role: message.role, text, sources, dataQuery, charts, webNotes: notes, reportDraft });
    }
  }
  const stage = session.stage || "gathering";
  return {
    turns,
    draft: null,
    gaps: [],
    steps: buildLedger(stage),
    stage,
    confirmToken: session.confirm_token ?? null,
    route: "",
    title: session.title ?? null,
    tags: session.tags ?? [],
  };
}

export function Chat() {
  return (
    <NeedPartnership>
      <ChatDesk />
    </NeedPartnership>
  );
}

function ChatDesk() {
  const desk = useDesk();
  const partnership = desk.partnership!;
  const [sid, setSid] = useState<string | null>(null);
  const [snap, setSnap] = useState<Snap | null>(null);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState("");
  const [activity, setActivity] = useState<Activity[]>([]);
  const activityRef = useRef<Activity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [model, setModel] = useState(() => localStorage.getItem("partnerdesk.model") || "");
  const threadRef = useRef<HTMLDivElement>(null);
  const bootKey = useRef("");

  function remember(nextSid: string, next: Snap) {
    snaps.set(nextSid, next);
    setSid(nextSid);
    setSnap(next);
  }

  async function openExisting(id: string) {
    const cached = snaps.get(id);
    if (cached) {
      setSid(id);
      setSnap(cached);
      return;
    }
    const session = await api<SessionRow>(`/api/sessions/${id}`);
    remember(id, fromSession(session, greeting(partnership)));
  }

  async function startNew() {
    const session = await postJson<SessionRow>("/api/sessions", { partnership_id: partnership.id });
    const fresh: Snap = {
      turns: [{ role: "assistant", text: greeting(partnership) }],
      draft: null, gaps: [], steps: [], stage: "gathering", confirmToken: null, route: "", title: null, tags: [],
    };
    remember(session.id, fresh);
    await desk.reloadSessions();
  }

  useEffect(() => {
    const jump = desk.jump;
    const key = `${partnership.id}:${jump.n}:${jump.sid}`;
    if (bootKey.current === key) return;
    bootKey.current = key;
    setError(null);
    const run = async () => {
      if (jump.n > 0 && jump.sid === "new") {
        await startNew();
        return;
      }
      if (jump.n > 0 && jump.sid) {
        await openExisting(jump.sid);
        return;
      }
      const rows = await api<{ id: string }[]>(`/api/sessions?partnership_id=${encodeURIComponent(partnership.id)}`);
      if (rows[0]) await openExisting(rows[0].id);
      else await startNew();
    };
    run().catch((err: Error) => setError(err.message));
    // startNew/openExisting close over the current partnership; jump is the trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partnership.id, desk.jump]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [snap?.turns, live, activity]);

  async function send(message: string, attached = files) {
    if (!sid || !snap || busy) return;
    const trimmed = message.trim();
    if (!trimmed && attached.length === 0) return;
    setBusy(true);
    setError(null);
    setText("");
    setFiles([]);
    const images: string[] = [];
    const documents: { name: string; data: string }[] = [];
    for (const file of attached) {
      const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
      if (IMAGE.has(ext) && images.length < (desk.meta?.max_images ?? 3)) images.push(await readDataUrl(file));
      else if (DOC.has(ext) && documents.length < 2) documents.push({ name: file.name, data: await readDataUrl(file) });
    }
    const userTurn: Turn = { role: "user", text: trimmed, images, files: documents.map((d) => d.name) };
    const base: Snap = { ...snap, turns: [...snap.turns, userTurn] };
    remember(sid, base);
    activityRef.current = [];
    setActivity([]);
    setLive("");
    let reply = "";
    const warnings: string[] = [];
    const finished: { event: Record<string, unknown> | null } = { event: null };
    let title = base.title;
    let tags = base.tags;
    let route = base.route;
    try {
      await streamMessage(sid, {
        message: trimmed,
        images,
        attachments: documents.map((d) => d.name),
        files: documents,
        model: model || null,
      }, (event) => {
        if (event.type === "status") {
          const next = applyActivity(activityRef.current, event);
          activityRef.current = next;
          setActivity(next);
        } else if (event.type === "token") {
          reply += String(event.text ?? "");
          setLive(reply);
        } else if (event.type === "session") {
          title = (event.title as string | null) ?? title;
          tags = (event.tags as string[]) ?? tags;
        } else if (event.type === "plan") {
          const tasks = (event.tasks as { name?: string }[]) ?? [];
          const names = tasks.map((task) => task.name).filter(Boolean);
          route = `→ ${names.join(" · ") || "answer"}`;
        } else if (event.type === "warning") {
          warnings.push(String(event.message ?? ""));
        } else if (event.type === "error") {
          throw new Error(String(event.message ?? "The model could not finish this reply."));
        } else if (event.type === "done") {
          finished.event = event;
        }
      });
      const done = finished.event;
      const next: Snap = {
        ...base,
        title,
        tags,
        route,
        turns: [...base.turns, {
          role: "assistant",
          text: reply || "(no reply from the model)",
          model: model || undefined,
          warnings,
          sources: citationSources(done?.brand_hits),
          dataQuery: dataQueryMark(done?.data_query),
          charts: reportCharts(done?.charts),
          webNotes: webNotes(done?.web_notes),
          reportDraft: done?.report_draft === true,
          activity: activityRef.current,
        }],
        stage: done ? String(done.stage ?? base.stage) : base.stage,
        draft: done ? ((done.draft as Draft | null) ?? null) : base.draft,
        gaps: done ? ((done.gaps_detail as Gap[] | undefined) ?? []) : base.gaps,
        steps: done ? ((done.steps as Step[] | undefined) ?? base.steps) : base.steps,
        confirmToken: done ? ((done.confirm_token as string | null) ?? null) : base.confirmToken,
      };
      remember(sid, next);
      activityRef.current = [];
      setActivity([]);
      setLive("");
      await desk.reloadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "send failed");
      setLive("");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!sid || !snap?.confirmToken || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await postJson<{ state: string; message?: string; draft?: Draft | null; steps?: Step[] }>(
        `/api/sessions/${sid}/confirm`,
        { token: snap.confirmToken },
      );
      const textOut = result.state === "submitted" || result.state === "changed"
        ? (result.message ?? "")
        : `Confirm: ${result.state}.`;
      const next: Snap = {
        ...snap,
        turns: [...snap.turns, { role: "assistant", text: textOut }],
        stage: result.state === "submitted" ? "submitted" : result.state === "changed" ? "gathering" : snap.stage,
        draft: result.state === "submitted" || result.state === "changed" ? (result.draft ?? null) : snap.draft,
        steps: result.state === "submitted" || result.state === "changed" ? (result.steps ?? []) : snap.steps,
        gaps: result.state === "submitted" || result.state === "changed" ? [] : snap.gaps,
        confirmToken: null,
      };
      remember(sid, next);
      await desk.reloadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "confirm failed");
    } finally {
      setBusy(false);
    }
  }

  function markFeedback(logId: number, feedback: "good" | "bad") {
    setSnap((current) => {
      if (!current || !sid) return current;
      const next: Snap = {
        ...current,
        turns: current.turns.map((turn) => (
          turn.dataQuery?.logId === logId ? { ...turn, dataQuery: { ...turn.dataQuery, feedback } } : turn
        )),
      };
      snaps.set(sid, next);
      return next;
    });
  }

  const ready = confirmReady(snap?.steps);
  const models = desk.meta?.models ?? [];
  const modelId = model || desk.meta?.default_model || models[0]?.id || "";

  return (
    <div className="chat-layout">
      <section className="thread-col">
        <header className="page-head">
          <h1>{partnership.brand_name} · {partnership.distributor_name}</h1>
          <p>ships to {partnership.ship_to_country || "—"} {snap?.route && <span className="route">{snap.route}</span>}</p>
        </header>
        {error && <div className="error">{error}</div>}
        <div className="thread" ref={threadRef}>
          {snap?.turns.map((turn, index) => (
            <div className={`turn ${turn.role}`} key={index}>
              <div className="bubble">
                {turn.activity && turn.activity.length > 0 && <ActivityList steps={turn.activity} />}
                {turn.model && <div className="who">{models.find((item) => item.id === turn.model)?.short || turn.model}</div>}
                {turn.reportDraft && <div className="draft-tag">Draft</div>}
                <p>{turn.text}</p>
                {turn.charts?.map((chart, chartIndex) => (
                  <ReportFigure key={`${chart.title}-${chartIndex}`} chart={chart} />
                ))}
                {turn.webNotes?.map((note) => <div className="source" key={note}>Public context · {note}</div>)}
                {turn.files?.map((name) => <div className="who" key={name}>📎 {name}</div>)}
                {turn.images?.map((src) => <img key={src.slice(0, 32)} alt="" src={src} />)}
                {turn.sources?.map((source) => (
                  <div className="source" key={source.n}>[{source.n}] {source.source}</div>
                ))}
                {turn.warnings?.map((warning) => <div className="warn" key={warning}>⚠ {warning}</div>)}
                {turn.role === "assistant" && turn.dataQuery && sid && (
                  <DataRating
                    sid={sid}
                    query={turn.dataQuery}
                    onSaved={(feedback) => markFeedback(turn.dataQuery!.logId, feedback)}
                  />
                )}
              </div>
            </div>
          ))}
          {(activity.length > 0 || live) && (
            <div className="turn assistant">
              <div className="bubble">
                {activity.length > 0 && <ActivityList steps={activity} />}
                {live && <p>{live}</p>}
              </div>
            </div>
          )}
        </div>
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            void send(text);
          }}
        >
          <textarea
            value={text}
            placeholder="An order, or attach an agreement (PDF / Word)…"
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send(text);
              }
            }}
          />
          <div className="row">
            <select
              aria-label="Model"
              value={modelId}
              onChange={(event) => {
                setModel(event.target.value);
                localStorage.setItem("partnerdesk.model", event.target.value);
              }}
            >
              {models.map((item) => <option key={item.id} value={item.id}>{item.short}</option>)}
            </select>
            <label className="btn">
              Attach
              <input
                type="file"
                multiple
                hidden
                accept=".png,.jpg,.jpeg,.webp,.pdf,.docx,.txt,.md"
                onChange={(event) => setFiles([...(event.target.files ?? [])])}
              />
            </label>
            {files.length > 0 && <span className="muted">{files.map((file) => file.name).join(", ")}</span>}
            <button className="btn-primary" type="submit" disabled={busy || !sid}>Send</button>
          </div>
        </form>
      </section>
      <aside className="draft-col">
        <div className="panel">
          <h2>Order draft</h2>
          <DraftView draft={snap?.draft ?? null} />
          {snap?.gaps.map((gap, index) => (
            <div key={index}>
              <div className="warn">{gap.message}</div>
              <div className="actions">
                {gap.candidates?.map((candidate) => (
                  <button key={candidate.sku} className="btn" type="button" onClick={() => void send(`I mean: ${candidate.name}`, [])}>
                    {candidate.name}
                  </button>
                ))}
              </div>
            </div>
          ))}
          {busy && activity.length > 0 && (
            <>
              <h2>这一轮</h2>
              <ActivityList steps={activity} />
            </>
          )}
          <h2>Progress</h2>
          <ul className="steps">
            {(snap?.steps?.length ? snap.steps : [
              { title: "Understand request", status: "pending" },
              { title: "Check inputs", status: "pending" },
              { title: "Confirm draft", status: "pending" },
              { title: "Create & submit order", status: "pending" },
            ]).map((step) => (
              <li key={step.title}>{STEP_MARK[step.status] ?? "·"} {step.title}</li>
            ))}
          </ul>
          {snap?.stage === "confirm" && snap.confirmToken && (
            <button className="btn-primary" type="button" disabled={!ready || busy} onClick={() => void confirm()}>
              Confirm — place the order
            </button>
          )}
          {snap?.stage === "submitted" && (
            <p className="ok-note">Order placed. Say “received” or “paid” when the time comes, or start another order.</p>
          )}
        </div>
      </aside>
    </div>
  );
}

function ActivityList({ steps }: { steps: Activity[] }) {
  return (
    <ol className="activity">
      {steps.map((step, index) => (
        <li key={step.id} className={step.state}>
          <span className="mark">{step.state === "done" ? "✓" : "…"}</span>
          <span className="title">{index + 1}. {step.title}</span>
          <span className="detail">{step.detail}</span>
        </li>
      ))}
    </ol>
  );
}

function DataRating({
  sid, query, onSaved,
}: {
  sid: string;
  query: DataQueryMark;
  onSaved: (feedback: "good" | "bad") => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [sql, setSql] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (query.feedback) {
    return <div className="source">{query.feedback === "good" ? "Marked right" : "Marked wrong"}</div>;
  }

  async function send(feedback: "good" | "bad") {
    setBusy(true);
    setError(null);
    try {
      await postJson(`/api/sessions/${sid}/data-feedback`, {
        log_id: query.logId,
        feedback,
        reason: feedback === "bad" ? reason : null,
        corrected_sql: feedback === "bad" ? sql : null,
      });
      onSaved(feedback);
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rate">
      <button type="button" className="btn" disabled={busy} onClick={() => void send("good")}>Looks right</button>
      <button type="button" className="btn" disabled={busy} onClick={() => setOpen(true)}>Wrong</button>
      {open && (
        <>
          <textarea
            className="rate-note"
            value={reason}
            placeholder="What was wrong (optional)"
            onChange={(event) => setReason(event.target.value)}
          />
          <textarea
            className="rate-note"
            value={sql}
            placeholder="Corrected SQL (optional)"
            onChange={(event) => setSql(event.target.value)}
          />
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void send("bad")}>Save</button>
        </>
      )}
      {error && <div className="warn">{error}</div>}
    </div>
  );
}

function DraftView({ draft }: { draft: Draft | null }) {
  if (!draft) return <p className="muted">Nothing yet.</p>;
  const cur = draft.currency;
  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Product</th><th>SKU</th><th>Cases</th><th>Price</th><th>Total</th><th /></tr>
          </thead>
          <tbody>
            {draft.lines.map((line, index) => (
              <tr key={index}>
                <td>{line.name}</td>
                <td>{line.sku || line.reference}</td>
                <td>{line.cases}</td>
                <td>{money(cur, line.case_price)}</td>
                <td>{money(cur, line.line_total)}</td>
                <td>{lineFlag(line)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        <strong>Payment</strong> {draft.payment_terms}<br />
        <strong>Incoterms</strong> {draft.incoterms}<br />
        <strong>Shipping</strong> {draft.shipping_method}
      </p>
      <p>
        <strong>ETA</strong> {draft.eta_date || "—"}<br />
        <strong>Discount</strong> {discountText(draft.discount, cur)}
        {draft.notes ? <><br /><strong>Notes</strong> {draft.notes}</> : null}
      </p>
      <h3>Total {money(cur, draft.total)}</h3>
    </>
  );
}
