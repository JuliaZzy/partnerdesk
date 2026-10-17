import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";

import { api } from "./api";
import { chatTitle, tagLabel, type Partnership } from "./format";

export type Meta = {
  models: { id: string; label: string; short: string; description: string; vision: boolean; thinking: boolean }[];
  default_model: string;
  product_statuses: string[];
  memory_kinds: Record<string, string>;
  max_images: number;
};

export type SessionSummary = {
  id: string;
  title: string | null;
  tags: string[];
  stage: string;
  updated_at: string;
};

type ChatJump = { n: number; sid: string | "new" };

type DeskValue = {
  ready: boolean;
  error: string | null;
  meta: Meta | null;
  partnerships: Partnership[];
  partnership: Partnership | null;
  choosePartnership: (id: string) => void;
  sessions: SessionSummary[];
  reloadSessions: () => Promise<void>;
  jump: ChatJump;
  openChat: (sid: string | "new") => void;
};

const DeskContext = createContext<DeskValue | null>(null);

export function useDesk(): DeskValue {
  const value = useContext(DeskContext);
  if (!value) throw new Error("useDesk outside the desk");
  return value;
}

const NAV = [
  ["/", "Chat"],
  ["/products", "Products"],
  ["/orders", "Orders"],
  ["/contracts", "Contracts"],
  ["/reports", "Reports"],
  ["/memory", "Memory"],
] as const;

export function DeskProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [partnerships, setPartnerships] = useState<Partnership[]>([]);
  const [pid, setPid] = useState<string | null>(() => localStorage.getItem("partnerdesk.partnership"));
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [jump, setJump] = useState<ChatJump>({ n: 0, sid: "" });
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    Promise.all([api<Meta>("/api/meta"), api<Partnership[]>("/api/partnerships")])
      .then(([nextMeta, nextPartnerships]) => {
        setMeta(nextMeta);
        setPartnerships(nextPartnerships);
        setPid((current) => (
          current && nextPartnerships.some((p) => p.id === current) ? current : nextPartnerships[0]?.id ?? null
        ));
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setReady(true));
  }, []);

  const partnership = partnerships.find((p) => p.id === pid) ?? null;

  const reloadSessions = useCallback(async () => {
    if (!pid) {
      setSessions([]);
      return;
    }
    setSessions(await api<SessionSummary[]>(`/api/sessions?partnership_id=${encodeURIComponent(pid)}`));
  }, [pid]);

  useEffect(() => {
    reloadSessions().catch((err: Error) => setError(err.message));
  }, [reloadSessions]);

  const value = useMemo<DeskValue>(() => ({
    ready,
    error,
    meta,
    partnerships,
    partnership,
    choosePartnership: (id: string) => {
      localStorage.setItem("partnerdesk.partnership", id);
      setPid(id);
      setJump({ n: 0, sid: "" });
    },
    sessions,
    reloadSessions,
    jump,
    openChat: (sid: string | "new") => {
      setJump((current) => ({ n: current.n + 1, sid }));
      navigate("/");
    },
  }), [ready, error, meta, partnerships, partnership, sessions, reloadSessions, jump, navigate]);

  return <DeskContext.Provider value={value}>{children}</DeskContext.Provider>;
}

export function Shell({ children }: { children: ReactNode }) {
  const desk = useDesk();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <strong>partnerdesk</strong>
          <span>desk</span>
        </div>
        {desk.partnerships.length > 1 && (
          <select
            aria-label="Partnership"
            value={desk.partnership?.id ?? ""}
            onChange={(event) => desk.choosePartnership(event.target.value)}
          >
            {desk.partnerships.map((p) => (
              <option key={p.id} value={p.id}>{p.brand_name} · {p.distributor_name}</option>
            ))}
          </select>
        )}
        {desk.partnership && desk.partnerships.length <= 1 && (
          <div className="side-note" style={{ color: "var(--sidebar-dim)", padding: "0 8px" }}>
            {desk.partnership.brand_name} · {desk.partnership.distributor_name}
          </div>
        )}
        <button className="btn-primary" type="button" onClick={() => desk.openChat("new")}>New chat</button>
        <nav className="nav">
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "on" : "")}>{label}</NavLink>
          ))}
        </nav>
        <div className="side-label">Chats</div>
        <div className="chat-list">
          {desk.sessions.map((session) => (
            <button key={session.id} type="button" onClick={() => desk.openChat(session.id)}>
              {chatTitle(session)}
              {tagLabel(session.tags) && <small>{tagLabel(session.tags)}</small>}
            </button>
          ))}
        </div>
      </aside>
      <main className="main">
        {desk.error && <div className="error">{desk.error}</div>}
        {children}
      </main>
    </div>
  );
}

export function NeedPartnership({ children }: { children: ReactNode }) {
  const { ready, partnership } = useDesk();
  if (!ready) return <p className="muted">Opening…</p>;
  if (!partnership) {
    return (
      <div className="empty">
        No brand–distributor partnership is set up yet, so there is nothing to order or report on.
        The product catalog is under Products.
      </div>
    );
  }
  return children;
}
