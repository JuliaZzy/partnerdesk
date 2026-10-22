import { useEffect, useRef, useState } from "react";

import { api, postJson, streamMessage } from "../api";
import { flightOf, isChatBusy, isForgotten, onForgetChat, patchFlight, setActiveChat, useFlight } from "../chat-live";
import { NeedPartnership, useDesk } from "../desk";
import {
  applyActivity,
  attachOutput,
  buildLedger,
  citationSources,
  confirmReady,
  contractFiling,
  dataQueryMark,
  discountText,
  greeting,
  lineFlag,
  looseModelJson,
  money,
  readActivity,
  trimNum,
  reportCharts,
  resultLink,
  resultPanes,
  webNotes,
  type Activity,
  type ContractFiling,
  type DataQueryMark,
  type DraftLine,
  type ReportChart,
  type Step,
} from "../format";
import { Markdown } from "../markdown-view";
import { DataTable, Tabs } from "../ui";

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
  contract?: ContractFiling;
  order?: boolean;
  activity?: Activity[];
  modelJson?: unknown[];
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
    model_json?: unknown;
    activity?: unknown;
    report_draft?: boolean;
    contract?: unknown;
    order?: boolean;
    order_draft?: unknown;
  }[];
};

const snaps = new Map<string, Snap>();
onForgetChat((id) => snaps.delete(id));
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

function orderDraft(raw: unknown): Draft | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const row = raw as Draft;
  return Array.isArray(row.lines) ? row : null;
}

function fromSession(session: SessionRow, hello: string): Snap {
  const turns: Turn[] = [{ role: "assistant", text: hello }];
  let draft: Draft | null = null;
  for (const message of session.history ?? []) {
    const text = String(message.content ?? "");
    if ((message.role === "user" || message.role === "assistant") && !SKIP.has(text)) {
      const sources = message.role === "assistant" ? citationSources(message.sources) : undefined;
      const dataQuery = message.role === "assistant" ? dataQueryMark(message.data_query) : undefined;
      const charts = message.role === "assistant" ? reportCharts(message.charts) : undefined;
      const notes = message.role === "assistant" ? webNotes(message.web_notes) : undefined;
      const reportDraft = message.role === "assistant" && message.report_draft === true;
      const contract = message.role === "assistant" ? contractFiling(message.contract) : undefined;
      const order = message.role === "assistant" && message.order === true;
      const savedDraft = orderDraft(message.order_draft);
      if (savedDraft) draft = savedDraft;
      const activity = message.role === "assistant" ? readActivity(message.activity) : undefined;
      const stored = message.role === "assistant" && Array.isArray(message.model_json) ? message.model_json : undefined;
      const modelJson = looseModelJson(stored, activity);
      turns.push({
        role: message.role, text, sources, dataQuery, charts, webNotes: notes,
        reportDraft, contract, order, activity, modelJson,
      });
    }
  }
  const stage = session.stage || "gathering";
  return {
    turns,
    draft,
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
  const [bootError, setBootError] = useState<string | null>(null);
  const [model, setModel] = useState(() => localStorage.getItem("partnerdesk.model") || "");
  const threadRef = useRef<HTMLDivElement>(null);
  const bootKey = useRef("");
  const sidRef = useRef<string | null>(null);
  const openSeq = useRef(0);
  // True while an IME preedit is still uncommitted. Enter confirms that
  // preedit; it must not also send, or the commit lands in the cleared box.
  const composing = useRef(false);
  const flight = useFlight(sid);
  sidRef.current = sid;
  const liveKind = flight.task === "report" || flight.task === "contract" || flight.task === "po" ? flight.task : null;
  const liveTask = liveKind ? { kind: liveKind, text: flight.live } : null;
  const panes = resultPanes({
    hasOrder: Boolean(snap?.draft),
    reports: (snap?.turns ?? []).flatMap((turn, index) => (
      turn.reportDraft ? [{ id: `report-${index}`, text: turn.text, charts: turn.charts }] : []
    )),
    contracts: (snap?.turns ?? []).flatMap((turn, index) => (
      turn.contract ? [{ id: `contract-${index}`, filing: turn.contract }] : []
    )),
    live: liveTask,
  });
  const paneKey = panes.map((pane) => pane.id).join("|");
  const [picked, setPicked] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const seenPanes = useRef("");
  const seenChat = useRef<string | null>(null);
  useEffect(() => {
    if (seenChat.current !== sid) {
      seenChat.current = sid;
      seenPanes.current = "";
      setPanelOpen(true);
    }
    const ids = paneKey ? paneKey.split("|") : [];
    const previous = seenPanes.current ? seenPanes.current.split("|") : [];
    const added = ids.filter((id) => !previous.includes(id));
    seenPanes.current = paneKey;
    if (added.length) {
      setPicked(added[added.length - 1]);
      setPanelOpen(true);
    } else setPicked((current) => (current && ids.includes(current) ? current : ids.at(-1) ?? null));
  }, [paneKey, sid]);
  const activePane = panes.find((pane) => pane.id === picked) ?? panes.at(-1) ?? null;

  function openResult(id: string) {
    setPicked(id);
    setPanelOpen(true);
  }

  function focus(nextSid: string, next: Snap) {
    if (isForgotten(nextSid)) return;
    snaps.set(nextSid, next);
    sidRef.current = nextSid;
    setSid(nextSid);
    setSnap(next);
    setActiveChat(nextSid);
  }

  function commitSnap(id: string, next: Snap) {
    if (isForgotten(id)) return;
    snaps.set(id, next);
    if (sidRef.current === id) setSnap(next);
  }

  async function openExisting(id: string, seq: number) {
    const cached = snaps.get(id);
    if (cached) {
      if (seq === openSeq.current) focus(id, cached);
      return;
    }
    const session = await api<SessionRow>(`/api/sessions/${id}`);
    if (seq !== openSeq.current) return;
    if (snaps.has(id)) {
      focus(id, snaps.get(id)!);
      return;
    }
    focus(id, fromSession(session, greeting(partnership)));
  }

  async function startNew(seq: number) {
    const session = await postJson<SessionRow>("/api/sessions", { partnership_id: partnership.id });
    if (seq !== openSeq.current) return;
    const fresh: Snap = {
      turns: [{ role: "assistant", text: greeting(partnership) }],
      draft: null, gaps: [], steps: [], stage: "gathering", confirmToken: null, route: "", title: null, tags: [],
    };
    focus(session.id, fresh);
    await desk.reloadSessions();
  }

  useEffect(() => onForgetChat((id) => {
    if (sidRef.current !== id) return;
    sidRef.current = null;
    setSid(null);
    setSnap(null);
  }), []);

  useEffect(() => {
    const jump = desk.jump;
    const key = `${partnership.id}:${jump.n}:${jump.sid}`;
    if (bootKey.current === key) return;
    bootKey.current = key;
    const seq = ++openSeq.current;
    setBootError(null);
    const run = async () => {
      if (jump.n > 0 && jump.sid === "new") {
        // Paint the switch before the create request. A reply still holding its
        // stream used to leave this click with nothing on screen until that request returned.
        sidRef.current = null;
        setSid(null);
        setActiveChat(null);
        setSnap({
          turns: [{ role: "assistant", text: "Opening…" }],
          draft: null, gaps: [], steps: [], stage: "gathering", confirmToken: null, route: "", title: null, tags: [],
        });
        await startNew(seq);
        return;
      }
      if (jump.n > 0 && jump.sid) {
        await openExisting(jump.sid, seq);
        return;
      }
      const rows = await api<{ id: string }[]>(`/api/sessions?partnership_id=${encodeURIComponent(partnership.id)}`);
      if (seq !== openSeq.current) return;
      if (rows[0]) await openExisting(rows[0].id, seq);
      else await startNew(seq);
    };
    run().catch((err: Error) => {
      if (seq === openSeq.current) setBootError(err.message);
    });
    // startNew/openExisting close over the current partnership; jump is the trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partnership.id, desk.jump]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [snap?.turns, flight.live, flight.activity]);

  const models = desk.meta?.models ?? [];
  const listed = models.some((item) => item.id === model);
  const modelId = (listed ? model : "") || desk.meta?.default_model || models[0]?.id || "";

  async function send(message: string, attached?: File[]) {
    const chatId = sidRef.current;
    const baseSnap = chatId ? snaps.get(chatId) : undefined;
    if (!chatId || !baseSnap || isChatBusy(chatId)) return;
    const chosen = attached ?? flightOf(chatId).files;
    const trimmed = message.trim();
    if (!trimmed && chosen.length === 0) return;
    patchFlight(chatId, { busy: true, error: null, draft: "", files: [], live: "", activity: [], modelJson: [], task: null });
    const images: string[] = [];
    const documents: { name: string; data: string }[] = [];
    for (const file of chosen) {
      const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
      if (IMAGE.has(ext) && images.length < (desk.meta?.max_images ?? 3)) images.push(await readDataUrl(file));
      else if (DOC.has(ext) && documents.length < 2) documents.push({ name: file.name, data: await readDataUrl(file) });
    }
    const userTurn: Turn = { role: "user", text: trimmed, images, files: documents.map((d) => d.name) };
    const base: Snap = { ...baseSnap, turns: [...baseSnap.turns, userTurn] };
    commitSnap(chatId, base);
    let steps: Activity[] = [];
    let reply = "";
    const warnings: string[] = [];
    const finished: { event: Record<string, unknown> | null } = { event: null };
    let title = base.title;
    let tags = base.tags;
    let route = base.route;
    const modelJson: unknown[] = [];
    // The reply is finished when `done` arrives. Waiting for the socket to close,
    // then for the chat list, kept "Replying…" up after the answer was already on screen.
    let committed = false;
    function finish() {
      if (committed) return;
      committed = true;
      const done = finished.event;
      const artifact = done?.report_draft === true || contractFiling(done?.contract) != null;
      const order = !artifact && orderDraft(done?.draft) != null;
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
          contract: contractFiling(done?.contract),
          order,
          activity: steps,
          modelJson: looseModelJson(modelJson, steps),
        }],
        stage: done ? String(done.stage ?? base.stage) : base.stage,
        draft: artifact ? base.draft : (done ? ((done.draft as Draft | null) ?? null) : base.draft),
        gaps: artifact ? base.gaps : (done ? ((done.gaps_detail as Gap[] | undefined) ?? []) : base.gaps),
        steps: artifact ? base.steps : (done ? ((done.steps as Step[] | undefined) ?? base.steps) : base.steps),
        confirmToken: artifact ? base.confirmToken : (done ? ((done.confirm_token as string | null) ?? null) : base.confirmToken),
      };
      commitSnap(chatId, next);
      patchFlight(chatId, { busy: false, live: "", activity: [], modelJson: [], task: null });
    }
    try {
      await streamMessage(chatId, {
        message: trimmed,
        images,
        attachments: documents.map((d) => d.name),
        files: documents,
        model: modelId || null,
      }, (event) => {
        if (event.type === "status") {
          steps = applyActivity(steps, event);
          patchFlight(chatId, { activity: steps });
        } else if (event.type === "token") {
          reply += String(event.text ?? "");
          patchFlight(chatId, { live: reply });
        } else if (event.type === "session") {
          title = (event.title as string | null) ?? title;
          tags = (event.tags as string[]) ?? tags;
          desk.nameSession(chatId, title, tags);
        } else if (event.type === "model_json") {
          modelJson.push(event.json);
          if (steps.length === 0) {
            patchFlight(chatId, { modelJson: [...modelJson] });
          } else {
            steps = attachOutput(steps, event.json);
            patchFlight(chatId, { activity: steps });
          }
        } else if (event.type === "plan") {
          const tasks = (event.tasks as { name?: string }[]) ?? [];
          const names = tasks.map((task) => String(task.name || "")).filter(Boolean);
          route = `→ ${names.join(" · ") || "answer"}`;
          const task = names.includes("report") ? "report" : names.includes("contract") ? "contract" : names.includes("po") ? "po" : null;
          if (task) patchFlight(chatId, { task });
        } else if (event.type === "warning") {
          warnings.push(String(event.message ?? ""));
        } else if (event.type === "error") {
          throw new Error(String(event.message ?? "The model could not finish this reply."));
        } else if (event.type === "done") {
          finished.event = event;
          finish();
        }
      });
      finish();
    } catch (err) {
      if (!committed) patchFlight(chatId, { error: err instanceof Error ? err.message : "send failed" });
    } finally {
      patchFlight(chatId, { busy: false, live: "", activity: [], modelJson: [], task: null });
    }
    void desk.reloadSessions().catch(() => undefined);
  }

  async function confirm() {
    const chatId = sidRef.current;
    const current = chatId ? snaps.get(chatId) : undefined;
    if (!chatId || !current?.confirmToken || isChatBusy(chatId)) return;
    patchFlight(chatId, { busy: true, error: null });
    try {
      const result = await postJson<{ state: string; message?: string; draft?: Draft | null; steps?: Step[] }>(
        `/api/sessions/${chatId}/confirm`,
        { token: current.confirmToken },
      );
      const textOut = result.state === "submitted" || result.state === "changed"
        ? (result.message ?? "")
        : `Confirm: ${result.state}.`;
      const next: Snap = {
        ...current,
        turns: [...current.turns, { role: "assistant", text: textOut }],
        stage: result.state === "submitted" ? "submitted" : result.state === "changed" ? "gathering" : current.stage,
        draft: result.state === "submitted" || result.state === "changed" ? (result.draft ?? null) : current.draft,
        steps: result.state === "submitted" || result.state === "changed" ? (result.steps ?? []) : current.steps,
        gaps: result.state === "submitted" || result.state === "changed" ? [] : current.gaps,
        confirmToken: null,
      };
      commitSnap(chatId, next);
      await desk.reloadSessions();
    } catch (err) {
      patchFlight(chatId, { error: err instanceof Error ? err.message : "confirm failed" });
    } finally {
      patchFlight(chatId, { busy: false });
    }
  }

  function markFeedback(logId: number, feedback: "good" | "bad") {
    const chatId = sidRef.current;
    const current = chatId ? snaps.get(chatId) : undefined;
    if (!chatId || !current) return;
    commitSnap(chatId, {
      ...current,
      turns: current.turns.map((turn) => (
        turn.dataQuery?.logId === logId ? { ...turn, dataQuery: { ...turn.dataQuery, feedback } } : turn
      )),
    });
  }

  const ready = confirmReady(snap?.steps);
  const showPanel = panelOpen && panes.length > 0;
  const livePane = liveKind === "report"
    ? panes.find((pane) => pane.id === "report-live")
    : liveKind === "contract"
      ? panes.find((pane) => pane.id === "contract-live")
      : liveKind === "po"
        ? panes.find((pane) => pane.kind === "order")
        : undefined;
  const reportNotes = activePane?.kind === "report" && activePane.id.startsWith("report-") && activePane.id !== "report-live"
    ? snap?.turns[Number(activePane.id.slice("report-".length))]?.webNotes
    : undefined;
  const panelTitle = activePane?.kind === "contract" && activePane.filing?.title
    ? activePane.filing.title
    : activePane?.label ?? "结果";

  return (
    <div className={`chat-layout${showPanel ? " with-result" : ""}`}>
      <section className="thread-col">
        <header className="page-head">
          <h1>{partnership.brand_name} · {partnership.distributor_name}</h1>
          <p>ships to {partnership.ship_to_country || "—"} {snap?.route && <span className="route">{snap.route}</span>}</p>
        </header>
        {(bootError || flight.error) && <div className="error">{bootError || flight.error}</div>}
        <div className="thread" ref={threadRef}>
          {snap?.turns.map((turn, index) => {
            const paneId = turn.reportDraft ? `report-${index}` : turn.contract ? `contract-${index}` : turn.order ? "order" : null;
            const pane = paneId ? panes.find((item) => item.id === paneId) ?? panes.find((item) => item.kind === (turn.order ? "order" : turn.reportDraft ? "report" : "contract")) : undefined;
            const artifact = Boolean(pane);
            return (
            <div className={`turn ${turn.role}`} key={index}>
              <div className="bubble">
                {turn.activity && turn.activity.length > 0 && <ActivityList steps={turn.activity} quiet={artifact} />}
                {turn.model && <div className="who">{models.find((item) => item.id === turn.model)?.short || turn.model}</div>}
                {artifact && pane
                  ? <button type="button" className="result-link" onClick={() => openResult(pane.id)}>{resultLink(pane.label)}</button>
                  : turn.role === "assistant"
                    ? <Markdown text={turn.text} charts={turn.charts} />
                    : <p>{turn.text}</p>}
                {!artifact && turn.webNotes && turn.webNotes.length > 0 && (
                  <div className="refs">
                    <div className="refs-label">参考资料：</div>
                    {turn.webNotes.map((note, noteIndex) => (
                      <div className="source" key={`${noteIndex}-${note}`}>[{noteIndex + 1}] {note}</div>
                    ))}
                  </div>
                )}
                {!artifact && turn.modelJson && turn.modelJson.length > 0 && <ModelJson items={turn.modelJson} />}
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
            );
          })}
          {(flight.activity.length > 0 || flight.live || flight.modelJson.length > 0 || liveKind) && (
            <div className="turn assistant">
              <div className="bubble">
                {flight.activity.length > 0 && <ActivityList steps={flight.activity} quiet={Boolean(liveKind)} />}
                {!liveKind && flight.modelJson.length > 0 && <ModelJson items={flight.modelJson} />}
                {livePane
                  ? <button type="button" className="result-link" onClick={() => openResult(livePane.id)}>{resultLink(livePane.label)}</button>
                  : flight.live && <Markdown text={flight.live} />}
              </div>
            </div>
          )}
        </div>
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            void send(flight.draft);
          }}
        >
          <textarea
            value={flight.draft}
            placeholder="An order, or attach an agreement (PDF / Word)…"
            onCompositionStart={() => {
              composing.current = true;
            }}
            onCompositionEnd={() => {
              composing.current = false;
            }}
            onChange={(event) => {
              if (sid) patchFlight(sid, { draft: event.target.value });
            }}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey) return;
              // keyCode 229: Chrome still delivering this Enter to the IME.
              // Safari reports isComposing false on that same key, so the ref counts too.
              const native = event.nativeEvent;
              if (native.isComposing || native.keyCode === 229 || composing.current) return;
              event.preventDefault();
              void send(flight.draft);
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
                onChange={(event) => {
                  if (sid) patchFlight(sid, { files: [...(event.target.files ?? [])] });
                }}
              />
            </label>
            {flight.files.length > 0 && <span className="muted">{flight.files.map((file) => file.name).join(", ")}</span>}
            <button className="btn-primary" type="submit" disabled={flight.busy || !sid}>Send</button>
          </div>
        </form>
      </section>
      {showPanel && (
        <aside className="result-float">
          <div className="result-bar">
            <h2>{panelTitle}</h2>
            <button type="button" className="result-close" aria-label="关闭" onClick={() => setPanelOpen(false)}>×</button>
          </div>
          <div className="panel">
            {panes.length > 1 && (
              <Tabs labels={panes.map((pane) => pane.label)} value={activePane?.label ?? ""} onChange={(label) => {
                const pane = panes.find((item) => item.label === label);
                if (pane) setPicked(pane.id);
              }} />
            )}
            {activePane?.kind === "order" && (
              activePane.pending
                ? <p className="muted">正在整理采购单…</p>
                : (
                  <>
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
                      <button className="btn-primary" type="button" disabled={!ready || flight.busy} onClick={() => void confirm()}>
                        Confirm — place the order
                      </button>
                    )}
                    {snap?.stage === "submitted" && (
                      <p className="ok-note">Order placed. Say “received” or “paid” when the time comes, or start another order.</p>
                    )}
                  </>
                )
            )}
            {activePane?.kind === "report" && (
              activePane.pending
                ? <p className="muted">正在写报告…</p>
                : (
                  <div className="result-doc">
                    <Markdown text={activePane.text} charts={activePane.charts} prose />
                    {reportNotes && reportNotes.length > 0 && (
                      <div className="refs">
                        <div className="refs-label">参考资料：</div>
                        {reportNotes.map((note, noteIndex) => (
                          <div className="source" key={`${noteIndex}-${note}`}>[{noteIndex + 1}] {note}</div>
                        ))}
                      </div>
                    )}
                  </div>
                )
            )}
            {activePane?.kind === "contract" && <ContractResult filing={activePane.filing} pending={activePane.pending} />}
          </div>
        </aside>
      )}
    </div>
  );
}

function ModelJson({ items }: { items: unknown[] }) {
  return (
    <div className="model-jsons">
      {items.map((item, index) => (
        <pre className="model-json" key={index}>
          <span className="label">模型输出 {index + 1}</span>
          {JSON.stringify(item, null, 2)}
        </pre>
      ))}
    </div>
  );
}

function StepMark({ state }: { state: Activity["state"] }) {
  if (state === "done") {
    return (
      <span className="step-mark done" aria-hidden="true">
        <svg viewBox="0 0 16 16">
          <circle cx="8" cy="8" r="6.25" />
          <path d="M4.7 8.15 7 10.35 11.3 5.7" />
        </svg>
      </span>
    );
  }
  return (
    <span className="step-mark spin" aria-hidden="true">
      {Array.from({ length: 8 }, (_, index) => (
        <i key={index} style={{ transform: `rotate(${index * 45}deg)` }} />
      ))}
    </span>
  );
}

function ActivityList({ steps, quiet }: { steps: Activity[]; quiet?: boolean }) {
  return (
    <ol className="activity">
      {steps.map((step, index) => (
        <li key={step.id} className={step.state}>
          <div className="activity-line">
            <span className="title">{index + 1}. {step.title}</span>
            <span className="detail">{step.detail}</span>
            <StepMark state={step.state} />
          </div>
          {!quiet && step.outputs.length > 0 && <ModelJson items={step.outputs} />}
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

function ContractResult({ filing, pending }: { filing: ContractFiling | null; pending?: boolean }) {
  if (pending || !filing) return <p className="muted">正在读取合同…</p>;
  const rules = filing.discount_rules ?? [];
  const moqs = filing.moqs ?? [];
  const prices = filing.price_list ?? [];
  const derived = filing.derived_rules ?? [];
  const caption = [
    filing.contract_type?.replaceAll("_", " "),
    filing.file_name,
    (filing.term_start_date || filing.term_end_date) ? `${filing.term_start_date || "?"} → ${filing.term_end_date || "?"}` : null,
    filing.auto_renewal === true ? "auto-renews" : filing.auto_renewal === false ? "no auto-renewal" : null,
    filing.exclusivity_type,
  ].filter(Boolean).join(" · ");
  return (
    <>
      {caption && <p className="muted">{caption}</p>}
      {filing.territory_text && <p><strong>Territory</strong> {filing.territory_text}</p>}
      {filing.territories && filing.territories.length > 0 && (
        <p>
          {filing.territories.map((territory) => (
            <span key={`${territory.kind}-${territory.country_code}`} className={`badge ${territory.kind === "allowed" ? "ok" : "bad"}`} style={{ marginRight: 6 }}>
              {territory.country_code}{territory.kind === "excluded" ? " excluded" : ""}
            </span>
          ))}
        </p>
      )}
      {filing.skipped && filing.skipped.length > 0 && <div className="warn">Skipped: {filing.skipped.join("; ")}</div>}
      <h3>Discount schedules</h3>
      {rules.length === 0 && <p className="muted">None extracted.</p>}
      {rules.map((rule, index) => (
        <div className="card" key={index}>
          <strong>{rule.title || "Discount rule"}</strong>
          {rule.section_reference ? ` · §${rule.section_reference}` : ""}
          <div className="muted">{(rule.applies_per || "").replaceAll("_", " ")} · {(rule.basis || "").replaceAll("_", " ")}</div>
          <DataTable
            rows={rule.tiers ?? []}
            columns={[
              { key: "containers", label: "Containers", render: (tier) => `${tier.from_container || 1} – ${tier.to_container ?? "∞"}` },
              { key: "discount", label: "Discount", render: (tier) => tier.discount_percent == null ? "—" : `${trimNum(tier.discount_percent)}%` },
            ]}
          />
        </div>
      ))}
      {moqs.length > 0 && (
        <>
          <h3>Minimum order quantities</h3>
          <DataTable rows={moqs} columns={Object.keys(moqs[0]).map((key) => ({ key, label: key }))} />
        </>
      )}
      {prices.length > 0 && (
        <>
          <h3>Price list</h3>
          <DataTable rows={prices} columns={Object.keys(prices[0]).map((key) => ({ key, label: key }))} />
        </>
      )}
      <h3>PO rules</h3>
      {derived.length === 0 ? <p className="muted">No rules derived.</p> : (
        <DataTable
          rows={derived}
          columns={[
            { key: "name", label: "Rule" },
            { key: "rule_type", label: "Type" },
            { key: "severity", label: "Severity" },
            { key: "rule_config", label: "Config", wrap: true, render: (row) => JSON.stringify(row.rule_config ?? {}) },
          ]}
        />
      )}
    </>
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
