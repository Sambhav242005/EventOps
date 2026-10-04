"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  clearSession,
  getEventId,
  loadMyEvents,
  loadSession,
  saveMyEvents,
  setEventId,
  DEFAULT_EVENT_ID,
  type MyEvent,
} from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const wsURL = (token: string, eventId: number) =>
  `${API.replace(/^http/, "ws")}/ws?token=${encodeURIComponent(token)}&event_id=${eventId}`;

function withEventId(path: string, eventId: number): string {
  // /api/myevents is the event list itself — it needs no scoping.
  if (path.startsWith("/api/myevents")) return path;
  if (path.includes("event_id=")) return path;
  return `${path}${path.includes("?") ? "&" : "?"}event_id=${eventId}`;
}

function normalizeEvents(data: unknown): MyEvent[] {
  if (Array.isArray(data)) return data as MyEvent[];
  if (data && typeof data === "object" && Array.isArray((data as { events?: unknown }).events)) {
    return (data as { events: MyEvent[] }).events;
  }
  return [];
}

type Msg = { sender: string; text: string; channel?: string };

const CHANNELS = [
  { id: "room", label: "general", hint: "Team chat + @agent" },
  { id: "vendors", label: "vendors", hint: "Vendor calls, replies, status" },
  { id: "alerts", label: "alerts", hint: "Weather, watch, countdown" },
] as const;

const AGENT_CMDS = [
  "@agent research backup caterers",
  "@agent draft a message to the team",
  "@agent draft a message to guests",
  "@agent call the caterer",
  "@agent export csv",
  "@agent what needs attention?",
];

type OutboundItem = {
  id: number;
  recipient: string;
  body: string;
  status: string;
  send_at?: string;
  approved_by?: string;
};

type CallItem = {
  id: number;
  vendor_id: number;
  status: string;
  conversation_id?: string;
  transcript?: string;
};

type Candidate = { id: number; name: string; price_hint?: number | null };
type AlertItem = { id: number; severity: string; text: string };
type Attendee = {
  id: number;
  name: string;
  phone?: string;
  qr_token?: string;
  checked_in?: number;
};

type EventData = {
  attendance: { checked: number; total: number };
  event?: { name?: string; venue?: string; date_time?: string; team_id?: number | null } | null;
  outbound?: OutboundItem[];
  candidates?: Candidate[];
  alerts?: AlertItem[];
  calls?: CallItem[];
  messages?: Msg[];
  attendees?: Attendee[];
};

function Empty({ text }: { text: string }) {
  return (
    <p className="rounded-xl bg-muted/60 px-3 py-4 text-center text-sm text-muted-foreground">
      {text}
    </p>
  );
}

const AUD_LABEL: Record<string, string> = {
  team: "Team",
  guests: "Guests",
  all: "Everyone",
  everyone: "Everyone",
  "team+guests": "Everyone",
};

function audLabel(recipient: string): string {
  const m = recipient.match(/^audience:(.+)$/);
  if (m) return AUD_LABEL[m[1]] ?? m[1];
  if (recipient.startsWith("to:")) return "Direct";
  return recipient || "—";
}

function relTime(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const mins = Math.round((t - Date.now()) / 60000);
  if (mins <= 0) return "due now";
  if (mins < 60) return `in ${mins} min`;
  const h = Math.floor(mins / 60);
  return `in ${h}h ${mins % 60}m`;
}

function StatusNote({ text }: { text: string }) {
  if (!text) return null;
  return (
    <p role="status" className="text-sm text-muted-foreground">
      {text}
    </p>
  );
}

function senderBubble(sender: string) {
  if (sender === "agent") return "border border-emerald-200 bg-emerald-50";
  if (sender === "vendor") return "border border-indigo-200 bg-indigo-50";
  if (sender === "system") return "border border-dashed bg-transparent italic text-muted-foreground";
  return "border border-transparent bg-muted";
}

export default function Room() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [role, setRole] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [channel, setChannel] = useState<string>("room");
  const [unread, setUnread] = useState<Record<string, number>>({});
  const channelRef = useRef("room");
  const [data, setData] = useState<EventData | null>(null);
  const [chatError, setChatError] = useState("");
  const [wsStatus, setWsStatus] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [siteOrigin, setSiteOrigin] = useState("");
  const ws = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // multi-event context (falls back to single-event mode when /api/myevents 404s)
  const [events, setEvents] = useState<MyEvent[]>([]);
  const [eventId, setEventIdState] = useState<number>(DEFAULT_EVENT_ID);
  const [multi, setMulti] = useState(true);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState("");
  const [switchTarget, setSwitchTarget] = useState<number | null>(null);
  const eventIdRef = useRef<number>(DEFAULT_EVENT_ID);

  // calls
  const [callLang, setCallLang] = useState<"en" | "hi">("en");
  const [callMsg, setCallMsg] = useState("");

  // scheduled outbound edit
  const [editId, setEditId] = useState<number | null>(null);
  const [editBody, setEditBody] = useState("");
  const [schedMsg, setSchedMsg] = useState("");

  // attendee phone
  const [attId, setAttId] = useState("");
  const [attPhone, setAttPhone] = useState("");
  const [attMsg, setAttMsg] = useState("");

  // research
  const [researchCat, setResearchCat] = useState("catering");
  const [researchMsg, setResearchMsg] = useState("");
  const [researchBusy, setResearchBusy] = useState(false);

  const authz = useCallback(
    () => ({ "Content-Type": "application/json", Authorization: `Bearer ${token}` }),
    [token]
  );

  const api = useCallback(
    async <T,>(path: string, body?: unknown): Promise<T> => {
      const r = await fetch(`${API}${withEventId(path, eventIdRef.current)}`, {
        method: body ? "POST" : "GET",
        headers: authz(),
        body: body ? JSON.stringify(body) : undefined,
      });
      if (r.status === 401) throw new Error("login required");
      if (!r.ok) {
        let message = `Request failed (${r.status}).`;
        try {
          const detail = (await r.json()) as { error?: string };
          if (detail.error) message = detail.error;
        } catch {
          // Keep the status-based message when the server did not return JSON.
        }
        throw new Error(message);
      }
      return (await r.json()) as T;
    },
    [authz]
  );

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      setData(await api<EventData>("/api/event"));
    } catch {
      /* session expired */
    }
  }, [api, token]);

  const push = useCallback((m: Msg) => {
    setMsgs((p) => [...p.slice(-200), m]);
  }, []);

  function connect(tok: string, eid: number) {
    if (reconnectTimer.current !== null) window.clearTimeout(reconnectTimer.current);
    ws.current?.close();
    setWsStatus("connecting");
    const sock = new WebSocket(wsURL(tok, eid));
    ws.current = sock;
    sock.onopen = () => { setWsStatus("connected"); setChatError(""); };
    sock.onclose = (event) => {
      setWsStatus("disconnected");
      if (event.code === 4401) {
        setChatError("Chat login expired or this account cannot access the event. Sign in again.");
        return;
      }
      if (ws.current === sock) {
        reconnectTimer.current = window.setTimeout(() => {
          if (ws.current === sock) connect(tok, eid);
        }, 2500);
      }
    };
    sock.onerror = () => setWsStatus("disconnected");
    sock.onmessage = (e: MessageEvent) => {
      const m = JSON.parse(String(e.data)) as { sender: string; text: string; channel?: string };
      const ch = m.channel ?? "room";
      push({ sender: m.sender, text: m.text, channel: ch });
      if (ch !== channelRef.current) setUnread((u) => ({ ...u, [ch]: (u[ch] ?? 0) + 1 }));
      refresh();
    };
  }

  const loadEventData = useCallback(
    async (tok: string, eid: number) => {
      const r = await fetch(`${API}${withEventId("/api/event", eid)}`, {
        headers: { Authorization: `Bearer ${tok}` },
      });
      if (r.status === 401) throw new Error("login required");
      if (!r.ok) throw new Error(r.status === 403 ? "You don’t have access to this event." : `Could not load event (${r.status}).`);
      const d = (await r.json()) as EventData;
      setMsgs(d.messages ?? []);
      setData(d);
    },
    [push]
  );

  async function switchEvent(nextId: number) {
    const id = Number(nextId);
    if (!Number.isFinite(id) || id === eventIdRef.current) return;
    setSwitchError("");
    setSwitchTarget(id);
    setSwitching(true);
    try {
      await loadEventData(token, id);
      eventIdRef.current = id;
      setEventIdState(id);
      setEventId(id);
      setUnread({});
      setSwitchTarget(null);
      connect(token, id);
    } catch (e) {
      setSwitchError(e instanceof Error ? e.message : "Could not load that event.");
    } finally {
      setSwitching(false);
    }
  }

  useEffect(() => {
    const s = loadSession();
    setSiteOrigin(window.location.origin);
    if (!s) {
      router.replace("/login");
      return;
    }
    const startId = Number(s.event_id) || getEventId() || DEFAULT_EVENT_ID;
    eventIdRef.current = startId;
    setEventIdState(startId);
    // Show cached event list instantly; the network read below corrects it.
    setEvents(loadMyEvents());
    setToken(s.token);
    setRole(s.role);
    setName(s.name);
    (async () => {
      let eid = startId;
      try {
        const me = await fetch(`${API}/api/myevents`, {
          headers: { Authorization: `Bearer ${s.token}` },
        });
        if (me.status === 404) {
          // Backend not yet updated — single-event mode, no switcher.
          setMulti(false);
          setEvents([]);
          saveMyEvents([]);
        } else if (me.ok) {
          const list = normalizeEvents(await me.json());
          setMulti(true);
          setEvents(list);
          saveMyEvents(list);
          if (list.length > 0 && !list.some((e) => Number(e.id) === Number(eid))) {
            eid = Number(list[0].id) || DEFAULT_EVENT_ID;
            eventIdRef.current = eid;
            setEventIdState(eid);
            setEventId(eid);
          }
        } else {
          // Non-404 failure: keep cached list, stay on current event id.
          setMulti(loadMyEvents().length > 0);
        }
      } catch {
        // Offline / old backend: keep cached list, stay on current event id.
        setMulti(loadMyEvents().length > 0);
      }
      connect(s.token, eid);
      try {
        await loadEventData(s.token, eid);
      } catch {
        router.replace("/login");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function logout() {
    ws.current?.close();
    clearSession();
    router.replace("/login");
  }

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [msgs]);
  useEffect(() => () => {
    if (reconnectTimer.current !== null) window.clearTimeout(reconnectTimer.current);
    const socket = ws.current;
    ws.current = null;
    socket?.close();
  }, []);

  function send() {
    if (!text.trim()) return;
    if (ws.current?.readyState !== WebSocket.OPEN) {
      setChatError("Chat is reconnecting. Wait for the room to reconnect, then send again.");
      return;
    }
    try {
      ws.current.send(JSON.stringify({ text, channel }));
      setText("");
      setUnread((u) => ({ ...u, [channel]: 0 }));
    } catch {
      setChatError("Message could not be sent. Check your connection and try again.");
    }
  }

  function switchChannel(id: string) {
    setChannel(id);
    channelRef.current = id;
    setUnread((u) => ({ ...u, [id]: 0 }));
  }

  const mentionMatch = text.match(/@[\w-]*$/);
  const suggestions = mentionMatch
    ? AGENT_CMDS.filter((c) => c.toLowerCase().startsWith(mentionMatch[0].toLowerCase()))
    : [];

  function applySuggestion(cmd: string) {
    setText(text.replace(/@[\w-]*$/, cmd));
  }

  async function approveCall(id: number) {
    setCallMsg("");
    try {
      const r = await api<{ ok: boolean; error?: string }>("/api/approve_call", { id, lang: callLang });
      setCallMsg(r.ok ? `Call #${id} approved (${callLang})` : r.error ?? "failed");
    } catch {
      setCallMsg("failed");
    }
    refresh();
  }

  async function saveEdit(id: number) {
    setSchedMsg("");
    try {
      const r = await api<{ ok: boolean; error?: string }>("/api/edit_outbound", { id, body: editBody });
      setSchedMsg(r.ok ? `Notice #${id} updated` : r.error ?? "failed");
      if (r.ok) {
        setEditId(null);
        setEditBody("");
      }
    } catch {
      setSchedMsg("failed");
    }
    refresh();
  }

  async function cancelOutbound(id: number) {
    setSchedMsg("");
    try {
      const r = await api<{ ok: boolean; error?: string }>("/api/cancel_outbound", { id });
      setSchedMsg(r.ok ? `Notice #${id} cancelled` : r.error ?? "failed");
    } catch {
      setSchedMsg("failed");
    }
    refresh();
  }

  async function setPhone() {
    setAttMsg("");
    const id = Number(attId);
    if (!id) {
      setAttMsg("enter a numeric attendee id");
      return;
    }
    try {
      const r = await api<{ ok: boolean; phone?: string; error?: string }>("/api/attendee_phone", {
        id,
        phone: attPhone,
      });
      setAttMsg(r.ok ? `Attendee #${id} phone set to ${r.phone}` : r.error ?? "failed");
    } catch {
      setAttMsg("failed (organizer only?)");
    }
    refresh();
  }

  async function runResearch() {
    setResearchBusy(true);
    setResearchMsg("");
    try {
      const r = await api<{ ranked?: unknown[]; error?: string }>("/api/research", {
        category: researchCat,
      });
      const n = Array.isArray(r.ranked) ? r.ranked.length : 0;
      setResearchMsg(`Research: ${researchCat} -> ${n} backups ranked.`);
    } catch {
      setResearchMsg("research failed");
    } finally {
      setResearchBusy(false);
      refresh();
    }
  }

  async function exportCsv() {
    const r = await fetch(`${API}${withEventId("/api/export/attendees", eventIdRef.current)}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const blob = await r.blob();
    window.open(URL.createObjectURL(blob), "_blank");
  }

  const outbound = data?.outbound ?? [];
  const pending = outbound.filter((o) => o.status === "pending");
  const scheduled = outbound.filter((o) => o.status === "approved");
  const sentCount = outbound.filter((o) => o.status === "sent").length;
  const cancelledCount = outbound.filter((o) => o.status === "cancelled").length;
  const calls = data?.calls ?? [];
  const pendingCalls = calls.filter((c) => c.status === "pending");
  const settledCalls = calls.length - pendingCalls.length;
  const cands = (data?.candidates ?? []).slice(0, 5);
  const candTotal = data?.candidates ?? [];
  const alerts = (data?.alerts ?? []).slice(0, 5);
  const highAlerts = alerts.filter((a) => a.severity === "high");

  return (
    <main className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6 lg:p-8">
      <header className="flex flex-col gap-4 border-b border-border/80 pb-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <span aria-hidden="true" className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm shadow-primary/25">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 11a8 8 0 0 1 16 0" /><path d="M2 11h20" /><path d="M12 11v9" /><circle cx="12" cy="20" r="1" fill="currentColor" />
            </svg>
          </span>
          <div className="min-w-0">
            <h1 className="font-display text-2xl font-extrabold leading-tight tracking-tight">EventOps</h1>
            <p className="text-sm text-muted-foreground">Live event operations</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
          {role && <Badge variant="muted">{`${name} · ${role}`}</Badge>}
          {multi && events.length > 0 && (
            <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <span className="sr-only">Switch event</span>
              <select
                value={eventId}
                onChange={(e) => switchEvent(Number(e.target.value))}
                disabled={switching}
                aria-label="Switch event"
                className="h-8 max-w-[220px] truncate rounded-full border border-input bg-white px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {events.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name}
                    {e.venue ? ` · ${e.venue}` : ""}
                    {e.date_time ? ` · ${new Date(e.date_time).toLocaleDateString()}` : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <nav aria-label="Event tools" className="flex items-center gap-1 rounded-full border bg-card p-1 text-sm">
            <Link href="/dashboard/schedule" className="rounded-full px-3 py-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">Schedule</Link>
            <Link href="/dashboard/alerts" className="rounded-full px-3 py-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">Alerts</Link>
            <Link href="/dashboard/manage" className="rounded-full px-3 py-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">Manage</Link>
          </nav>
          <Button size="sm" variant="secondary" onClick={logout}>
            Logout
          </Button>
        </div>
      </header>

      {switchError && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          <span>{switchError} Your current event is still open.</span>
          <Button size="sm" variant="secondary" onClick={() => switchTarget && switchEvent(switchTarget)}>Retry event load</Button>
        </div>
      )}

      {data?.event && (
        <section aria-label="Selected event" className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border bg-card px-5 py-4 shadow-sm">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-primary">Now coordinating · {data.event.team_id == null ? "All teams" : `Team ${data.event.team_id}`}</p>
            <h2 className="mt-1 truncate font-display text-xl font-bold tracking-tight">{data.event.name}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{[data.event.venue, data.event.date_time ? new Date(data.event.date_time).toLocaleString() : ""].filter(Boolean).join(" · ") || "Event details not set"}</p>
          </div>
          <Link href="/dashboard/manage" className="text-sm font-semibold text-primary hover:underline">Manage event →</Link>
        </section>
      )}

      {!token ? (
        <Empty text="Opening your dashboard…" />
      ) : (
        <>
        {data && (
          <section aria-label="Event overview" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { label: "Attendance", value: `${data.attendance.checked}/${data.attendance.total}`, detail: "checked in" },
              { label: "Approvals", value: pending.length, detail: "need review" },
              { label: "Scheduled", value: scheduled.length, detail: "queued notices" },
              { label: "Alerts", value: alerts.length, detail: "open signals" },
            ].map((stat) => (
              <div key={stat.label} className="rounded-2xl border bg-card px-4 py-3 shadow-sm">
                <p className="text-xs font-medium text-muted-foreground">{stat.label}</p>
                <p className="mt-1 font-display text-xl font-bold tabular-nums">{stat.value}</p>
                <p className="text-xs text-muted-foreground">{stat.detail}</p>
              </div>
            ))}
          </section>
        )}
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.68fr)]">
          <Card className="min-w-0 overflow-hidden">
            <div className="flex min-h-[52vh] flex-col sm:min-h-[58vh] sm:flex-row">
              <nav aria-label="Channels" className="flex w-full shrink-0 gap-1 overflow-x-auto border-b bg-muted/40 p-2 sm:w-40 sm:flex-col sm:overflow-visible sm:border-b-0 sm:border-r">
                {CHANNELS.map((c) => {
                  const active = channel === c.id;
                  const n = unread[c.id] ?? 0;
                  return (
                    <button
                      key={c.id}
                      onClick={() => switchChannel(c.id)}
                      aria-current={active}
                      className={cn(
                        "flex w-auto shrink-0 items-center gap-1.5 rounded-xl px-2.5 py-2 text-left text-sm transition-colors sm:w-full",
                        active ? "bg-card font-semibold shadow-sm" : "text-muted-foreground hover:bg-card/60"
                      )}
                    >
                      <span aria-hidden="true" className="text-muted-foreground">
                        #
                      </span>
                      <span className="min-w-0 flex-1 truncate">{c.label}</span>
                      {n > 0 && (
                        <span className="grid h-5 min-w-[20px] place-items-center rounded-full bg-primary px-1.5 text-[11px] font-bold text-primary-foreground">
                          {n}
                        </span>
                      )}
                    </button>
                  );
                })}
                <p className="hidden px-2.5 pt-1 text-xs text-muted-foreground sm:block">
                  {CHANNELS.find((c) => c.id === channel)?.hint}
                </p>
              </nav>
              <div className="flex min-w-0 flex-1 flex-col">
                <div className="border-b px-4 py-2.5">
                  <p className="font-display text-base font-bold">
                    <span aria-hidden="true" className="text-muted-foreground">
                      #{" "}
                    </span>
                    {CHANNELS.find((c) => c.id === channel)?.label}
                  </p>
                </div>
                <div
                  ref={logRef}
                  aria-live="polite"
                  className="chat-scroll flex max-h-[46vh] min-h-[240px] flex-1 flex-col gap-2 overflow-y-auto p-3"
                >
                  {msgs.filter((m) => (m.channel ?? "room") === channel).length === 0 && (
                    <Empty
                      text={
                        channel === "room"
                          ? "No messages yet. Say hello — or try @agent below."
                          : channel === "vendors"
                            ? "Vendor calls, replies, and status changes land here."
                            : "Weather, vendor-watch, and countdown alerts land here."
                      }
                    />
                  )}
                  {msgs
                    .filter((m) => (m.channel ?? "room") === channel)
                    .map((m, i) => (
                      <div
                        key={i}
                        className={cn(
                          "min-w-0 break-words rounded-2xl px-3 py-2 text-sm",
                          senderBubble(m.sender)
                        )}
                      >
                        <span className="font-semibold">{m.sender}: </span>
                        {m.text}
                      </div>
                    ))}
                </div>
                <div className="relative border-t p-3">
                  {suggestions.length > 0 && (
                    <div
                      role="listbox"
                      aria-label="Agent command suggestions"
                      className="absolute inset-x-3 bottom-full mb-1 overflow-hidden rounded-2xl border bg-card shadow-lg"
                    >
                      {suggestions.map((s) => (
                        <button
                          key={s}
                          role="option"
                          aria-selected="false"
                          onClick={() => applySuggestion(s)}
                          className="block w-full truncate px-3 py-2 text-left font-mono text-sm hover:bg-muted"
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <Input
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") send();
                        if (e.key === "Escape") setText(text.replace(/@[\w-]*$/, ""));
                      }}
                      placeholder={`Message #${CHANNELS.find((c) => c.id === channel)?.label}… type @ for agent commands`}
                      aria-label="Chat message"
                      className="min-w-0"
                    />
                    <Button onClick={send} disabled={!text.trim() || wsStatus !== "connected"} className="shrink-0">
                      {wsStatus === "connected" ? "Send" : "Connecting…"}
                    </Button>
                  </div>
                  <p role="status" className="mt-1.5 text-xs text-muted-foreground">
                    {wsStatus === "connected" ? "Connected" : "Chat reconnecting — messages are not sent yet."}
                  </p>
                  {chatError && <p role="alert" className="mt-1 text-xs text-rose-700">{chatError}</p>}
                </div>
              </div>
            </div>
          </Card>

          <div className="flex min-w-0 flex-col gap-4">
            <Card>
              <CardHeader>
                <CardTitle>
                  Pending approvals{" "}
                  <Badge variant="muted" aria-label={`${pending.length} pending`}>
                    {pending.length}
                  </Badge>
                </CardTitle>
                <p className="text-sm text-muted-foreground">
                  Outbound notices waiting for a go-ahead.
                </p>
              </CardHeader>
              <CardContent className="space-y-2">
                {pending.length === 0 && <Empty text="All caught up — nothing waiting for approval." />}
                {pending.map((o) => (
                  <div key={o.id} className="rounded-xl border p-2.5 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <p className="min-w-0 truncate font-semibold">
                        #{o.id} <span className="font-normal">{audLabel(o.recipient)}</span>
                      </p>
                      <Badge variant="warning">pending</Badge>
                    </div>
                    <p className="mt-1 min-w-0 break-words text-muted-foreground">{o.body}</p>
                    <Button
                      size="sm"
                      className="mt-2 w-full"
                      onClick={() => api("/api/approve", { id: o.id }).then(refresh)}
                    >
                      {`Approve #${o.id}`}
                    </Button>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>
                  Scheduled{" "}
                  <Badge variant="muted" aria-label={`${scheduled.length} scheduled`}>
                    {scheduled.length}
                  </Badge>
                </CardTitle>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm text-muted-foreground">Approved notices, not yet sent.</p>
                  <Link href="/dashboard/schedule" className="text-sm font-medium text-primary hover:underline">Manage schedule →</Link>
                </div>
                {(sentCount > 0 || cancelledCount > 0) && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {sentCount > 0 && <Badge variant="success">{`${sentCount} sent`}</Badge>}
                    {cancelledCount > 0 && <Badge variant="muted">{`${cancelledCount} cancelled`}</Badge>}
                  </div>
                )}
              </CardHeader>
              <CardContent className="space-y-2">
                {scheduled.length === 0 && (
                  <Empty text="Nothing scheduled. Approved notices will wait here before sending." />
                )}
                {scheduled.map((o) => (
                  <div key={o.id} className="rounded-xl border p-2.5 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-semibold">
                        #{o.id} <span className="font-normal">{audLabel(o.recipient)}</span>
                      </p>
                      <Badge variant="default">scheduled</Badge>
                    </div>
                    {o.send_at && (
                      <p className="mt-1 text-xs text-muted-foreground">{`Sends ${relTime(o.send_at)}`}</p>
                    )}
                    {editId === o.id ? (
                      <div className="mt-2 space-y-2">
                        <Input
                          value={editBody}
                          onChange={(e) => setEditBody(e.target.value)}
                          placeholder="Edited body"
                          aria-label={`Edited body for notice ${o.id}`}
                        />
                        <div className="flex gap-2">
                          <Button size="sm" onClick={() => saveEdit(o.id)}>
                            Save
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setEditId(null);
                              setEditBody("");
                            }}
                          >
                            Discard
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <p className="mt-1 line-clamp-3 text-muted-foreground">{o.body}</p>
                        <div className="mt-2 flex gap-2">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => {
                              setEditId(o.id);
                              setEditBody(o.body);
                            }}
                          >
                            Edit
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => cancelOutbound(o.id)}>
                            Cancel
                          </Button>
                        </div>
                      </>
                    )}
                  </div>
                ))}
                <StatusNote text={schedMsg} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>
                  Pending calls{" "}
                  <Badge variant="muted" aria-label={`${pendingCalls.length} pending calls`}>
                    {pendingCalls.length}
                  </Badge>
                </CardTitle>
                <p className="text-sm text-muted-foreground">Vendor calls awaiting approval.</p>
                {settledCalls > 0 && (
                  <div className="pt-1">
                    <Badge variant="success">{`${settledCalls} settled`}</Badge>
                  </div>
                )}
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex items-center gap-2">
                  <label htmlFor="call-lang" className="text-sm text-muted-foreground">
                    Lang
                  </label>
                  <select
                    id="call-lang"
                    value={callLang}
                    onChange={(e) => setCallLang(e.target.value === "hi" ? "hi" : "en")}
                    className="rounded-full border border-input bg-white px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <option value="en">en</option>
                    <option value="hi">hi</option>
                  </select>
                </div>
                {pendingCalls.length === 0 && <Empty text="No vendor calls waiting for approval." />}
                {pendingCalls.map((c) => (
                  <div key={c.id} className="rounded-xl border p-2.5 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">
                        Call #{c.id} <span className="font-normal text-muted-foreground">(vendor {c.vendor_id})</span>
                      </span>
                      <Badge variant="warning">pending</Badge>
                    </div>
                    {role === "organizer" ? (
                      <Button size="sm" className="mt-2 w-full" onClick={() => approveCall(c.id)}>
                        Approve call
                      </Button>
                    ) : (
                      <p className="mt-2 text-xs text-muted-foreground">Organizer approval needed.</p>
                    )}
                  </div>
                ))}
                <StatusNote text={callMsg} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Backups & alerts</CardTitle>
                <p className="text-sm text-muted-foreground">Fallback vendors and event risks.</p>
              </CardHeader>
              <CardContent className="space-y-2">
                {cands.length === 0 && candTotal.length === 0 ? (
                  <Empty text="No backup vendors ranked yet. Run research by category below." />
                ) : (
                  <div className="flex flex-wrap gap-1">
                    {cands.map((c) => (
                      <Badge key={c.id} variant="muted">{`${c.name} ₹${c.price_hint ?? "?"}`}</Badge>
                    ))}
                  </div>
                )}
                {highAlerts.length > 0 && (
                  <div role="alert" className="space-y-1.5 rounded-xl border border-rose-200 bg-rose-50/70 p-2.5">
                    <p className="text-xs font-semibold uppercase tracking-wide text-rose-700">
                      Needs attention
                    </p>
                    {highAlerts.map((a) => (
                      <p key={a.id} className="text-sm text-rose-900">
                        <Badge variant="danger">high</Badge> {a.text}
                      </p>
                    ))}
                  </div>
                )}
                {alerts
                  .filter((a) => a.severity !== "high")
                  .map((a) => (
                    <p key={a.id} className="text-sm">
                      <Badge variant="muted">{a.severity}</Badge> {a.text}
                    </p>
                  ))}
                {alerts.length === 0 && <Empty text="No alerts. Risks will surface here." />}
                <Link href="/dashboard/alerts" className="inline-flex text-sm font-medium text-primary hover:underline">
                  View all alerts →
                </Link>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="ghost" onClick={refresh}>Refresh</Button>
                </div>
                <details className="rounded-xl border px-3 py-2">
                  <summary className="cursor-pointer text-xs font-medium text-muted-foreground">Demo simulation</summary>
                  <Button size="sm" variant="secondary" className="mt-2" onClick={() => api("/api/vendor_status", { id: 1, status: "cancelled" }).then(refresh)}>
                    Simulate caterer cancellation
                  </Button>
                </details>
                <div className="flex gap-2">
                  <Input
                    value={researchCat}
                    onChange={(e) => setResearchCat(e.target.value)}
                    placeholder="category"
                    aria-label="Research category"
                  />
                  <Button size="sm" onClick={runResearch} disabled={researchBusy || !researchCat.trim()} className="shrink-0 self-center">
                    {researchBusy ? "…" : "Research"}
                  </Button>
                </div>
                <StatusNote text={researchMsg} />
              </CardContent>
            </Card>

            <Card className="order-first border-primary/20 shadow-md shadow-primary/5">
              <CardHeader>
                <CardTitle>Guest check-in</CardTitle>
                <p className="text-sm text-muted-foreground">One QR for fast entrance check-in.</p>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-col items-center gap-4 rounded-2xl border bg-muted/30 p-4 sm:flex-row sm:items-start">
                  {siteOrigin && <img src={`${API}/api/event-qr/${eventId}?origin=${encodeURIComponent(siteOrigin)}`} alt="Guest check-in QR code" width={176} height={176} className="h-40 w-40 shrink-0 rounded-xl border bg-white p-1 shadow-sm" />}
                  <div className="min-w-0 flex-1 space-y-3 text-center sm:text-left">
                    <div>
                      <p className="font-semibold">Entrance check-in</p>
                      <p className="mt-1 text-sm text-muted-foreground">Guests scan, enter their details, and attendance is recorded instantly.</p>
                    </div>
                    <div className="flex w-full gap-2">
                      <Input readOnly value={siteOrigin ? `${siteOrigin}/join?event_id=${eventId}&door=1` : "Preparing event link…"} aria-label="Guest check-in link" className="min-w-0 bg-background text-xs" />
                      <Button size="sm" variant="secondary" onClick={() => navigator.clipboard.writeText(`${siteOrigin}/join?event_id=${eventId}&door=1`)} disabled={!siteOrigin} className="shrink-0">Copy link</Button>
                    </div>
                    {(siteOrigin.includes("localhost") || siteOrigin.includes("127.0.0.1")) && <p className="text-left text-xs text-amber-800">Localhost QR works only on this computer. Use a network-accessible URL before sharing with guests.</p>}
                  </div>
                </div>
                {(data?.attendees ?? []).length > 0 && role === "organizer" && (
                  <details className="rounded-xl border px-3 py-2">
                    <summary className="cursor-pointer text-sm font-medium">Attendee roster tools</summary>
                    <div className="mt-3 grid gap-2">
                    <label htmlFor="att-select" className="text-sm text-muted-foreground">
                      Set participant number
                    </label>
                    <select
                      id="att-select"
                      value={attId}
                      onChange={(e) => setAttId(e.target.value)}
                      className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <option value="">Choose attendee…</option>
                      {(data?.attendees ?? []).map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                          {a.checked_in ? " ✓" : ""}
                          {a.phone ? ` · ${a.phone}` : ""}
                        </option>
                      ))}
                    </select>
                    <Input
                      value={attPhone}
                      onChange={(e) => setAttPhone(e.target.value)}
                      placeholder="+91…"
                      inputMode="tel"
                      aria-label="Attendee phone"
                    />
                    <Button size="sm" variant="secondary" onClick={setPhone} disabled={!attId}>
                      Set phone
                    </Button>
                    </div>
                  </details>
                )}
                <StatusNote text={attMsg} />
                <Button size="sm" variant="secondary" onClick={exportCsv}>
                  Export attendees CSV
                </Button>
              </CardContent>
            </Card>
          </div>
        </div>
        </>
      )}
    </main>
  );
}
