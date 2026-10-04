"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { clearSession, loadSession } from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const wsURL = (token: string) =>
  `${API.replace(/^http/, "ws")}/ws?token=${encodeURIComponent(token)}`;

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
  event?: { name?: string; venue?: string; date_time?: string } | null;
  outbound?: OutboundItem[];
  candidates?: Candidate[];
  alerts?: AlertItem[];
  calls?: CallItem[];
  messages?: Msg[];
  attendees?: Attendee[];
};

type PhotoReview = { name: string; confidence: number };
type PhotoResp = { ok: boolean; marked?: string[]; review?: PhotoReview[]; error?: string };

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const s = String(r.result ?? "");
      resolve(s.includes(",") ? s.split(",")[1] : s);
    };
    r.onerror = () => reject(new Error("read failed"));
    r.readAsDataURL(file);
  });
}
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
  const [door, setDoor] = useState("");
  const [qr, setQr] = useState("");
  const ws = useRef<WebSocket | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // photo check-in
  const [photoMarked, setPhotoMarked] = useState<string[]>([]);
  const [photoReview, setPhotoReview] = useState<PhotoReview[]>([]);
  const [photoMsg, setPhotoMsg] = useState("");
  const [photoBusy, setPhotoBusy] = useState(false);

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
      const r = await fetch(`${API}${path}`, {
        method: body ? "POST" : "GET",
        headers: authz(),
        body: body ? JSON.stringify(body) : undefined,
      });
      if (r.status === 401) throw new Error("login required");
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

  function connect(tok: string) {
    const sock = new WebSocket(wsURL(tok));
    ws.current = sock;
    sock.onmessage = (e: MessageEvent) => {
      const m = JSON.parse(String(e.data)) as { sender: string; text: string; channel?: string };
      const ch = m.channel ?? "room";
      push({ sender: m.sender, text: m.text, channel: ch });
      if (ch !== channelRef.current) setUnread((u) => ({ ...u, [ch]: (u[ch] ?? 0) + 1 }));
      refresh();
    };
  }

  useEffect(() => {
    const s = loadSession();
    if (!s) {
      router.replace("/login");
      return;
    }
    setToken(s.token);
    setRole(s.role);
    setName(s.name);
    connect(s.token);
    fetch(`${API}/api/event`, { headers: { Authorization: `Bearer ${s.token}` } })
      .then((x) => x.json() as Promise<EventData>)
      .then((d) => {
        (d.messages ?? []).forEach(push);
        setData(d);
      })
      .catch(() => router.replace("/login"));
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
  useEffect(() => () => ws.current?.close(), []);

  function send() {
    if (!text.trim() || ws.current?.readyState !== 1) return;
    ws.current.send(JSON.stringify({ text, channel }));
    setText("");
    setUnread((u) => ({ ...u, [channel]: 0 }));
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

  async function checkin() {
    try {
      const r = await api<{ ok: boolean; duplicate?: boolean; name?: string; error?: string }>(
        "/api/checkin",
        { token: qr }
      );
      setDoor(
        r.duplicate ? `Already checked in (${r.name})` : r.ok ? `Checked in: ${r.name}` : r.error ?? "failed"
      );
    } catch {
      setDoor("failed");
    }
    refresh();
  }

  async function onPhotoFile(f: File | undefined) {
    if (!f) return;
    setPhotoBusy(true);
    setPhotoMsg("");
    try {
      const b64 = await fileToBase64(f);
      const r = await api<PhotoResp>("/api/photo_checkin", { image_base64: b64 });
      if (!r.ok) {
        setPhotoMsg(r.error ?? "photo check-in failed");
      } else {
        setPhotoMarked(r.marked ?? []);
        setPhotoReview(r.review ?? []);
        setPhotoMsg(`Marked ${(r.marked ?? []).length}, needs review ${(r.review ?? []).length}`);
      }
    } catch (e) {
      setPhotoMsg(e instanceof Error ? e.message : "photo check-in failed");
    } finally {
      setPhotoBusy(false);
      refresh();
    }
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
    const r = await fetch(`${API}/api/export/attendees`, {
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
    <main className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span
          aria-hidden="true"
          className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-primary-foreground"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 11a8 8 0 0 1 16 0" />
            <path d="M2 11h20" />
            <path d="M12 11v9" />
            <circle cx="12" cy="20" r="1" fill="currentColor" />
          </svg>
        </span>
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-extrabold leading-tight tracking-tight">
            EventOps Agent
          </h1>
          <p className="text-sm text-muted-foreground">Live coordination room for the event team</p>
        </div>
        <div className="ms-auto flex flex-wrap items-center gap-2">
          {role && <Badge>{`${name} · ${role}`}</Badge>}
          {data && (
            <Badge variant="success">{`✓ ${data.attendance.checked}/${data.attendance.total} in`}</Badge>
          )}
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            Home
          </Link>
          <Link href="/about" className="text-sm text-muted-foreground hover:text-foreground">
            About
          </Link>
          <Button size="sm" variant="secondary" onClick={logout}>
            Logout
          </Button>
        </div>
      </header>

      {data?.event && (
        <div className="flex flex-wrap items-center gap-2 rounded-2xl border bg-card px-4 py-2.5 text-sm">
          <span className="font-display font-bold">{data.event.name}</span>
          {data.event.venue && <span className="text-muted-foreground">· {data.event.venue}</span>}
          {data.event.date_time && (
            <Badge variant="muted">{new Date(data.event.date_time).toLocaleString()}</Badge>
          )}
        </div>
      )}

      {!token ? (
        <Empty text="Opening your dashboard…" />
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <Card className="min-w-0 overflow-hidden">
            <div className="flex min-h-[46vh]">
              <nav aria-label="Channels" className="w-36 shrink-0 space-y-1 border-r bg-muted/40 p-2 sm:w-44">
                {CHANNELS.map((c) => {
                  const active = channel === c.id;
                  const n = unread[c.id] ?? 0;
                  return (
                    <button
                      key={c.id}
                      onClick={() => switchChannel(c.id)}
                      aria-current={active}
                      className={cn(
                        "flex w-full items-center gap-1.5 rounded-xl px-2.5 py-2 text-left text-sm transition-colors",
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
                <p className="px-2.5 pt-1 text-xs text-muted-foreground">
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
                    <Button onClick={send} disabled={!text.trim()} className="shrink-0">
                      Send
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          </Card>

          <div className="min-w-0 space-y-4">
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
                <p className="text-sm text-muted-foreground">Approved notices, not yet sent.</p>
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
                <CardTitle>Door photo check-in</CardTitle>
                <p className="text-sm text-muted-foreground">
                  Snap the paper sign-in sheet at the gate. AI reads the handwritten
                  names — clear matches are marked present, unsure ones wait for you below.
                </p>
              </CardHeader>
              <CardContent className="space-y-2">
                <Input
                  type="file"
                  accept="image/*"
                  disabled={photoBusy}
                  aria-label="Upload door photo"
                  onChange={(e) => onPhotoFile(e.target.files?.[0])}
                />
                {!photoMsg && photoMarked.length === 0 && photoReview.length === 0 && (
                  <Empty text="No photo processed yet." />
                )}
                <StatusNote text={photoBusy ? "Reading photo…" : photoMsg} />
                {photoMarked.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {photoMarked.map((n) => (
                      <Badge key={n} variant="success">
                        {n}
                      </Badge>
                    ))}
                  </div>
                )}
                {photoReview.length > 0 && (
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground">
                      Needs a human — tap a name in the room list to confirm:
                    </p>
                    {photoReview.map((r, i) => (
                      <p key={`${r.name}-${i}`} className="text-sm">
                        <Badge variant="warning">{r.confidence.toFixed(2)}</Badge> {r.name || "(unreadable)"}
                      </p>
                    ))}
                  </div>
                )}
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
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      api("/api/vendor_status", { id: 1, status: "cancelled" }).then(refresh)
                    }
                  >
                    Simulate: caterer cancels
                  </Button>
                  <Button size="sm" variant="ghost" onClick={refresh}>
                    Refresh
                  </Button>
                </div>
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

            <Card>
              <CardHeader>
                <CardTitle>Door</CardTitle>
                <p className="text-sm text-muted-foreground">Check attendees in and manage records.</p>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex gap-2">
                  <Input
                    value={qr}
                    onChange={(e) => setQr(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && checkin()}
                    placeholder="QR token"
                    aria-label="QR token"
                  />
                  <Button size="sm" onClick={checkin} disabled={!qr.trim()} className="shrink-0 self-center">
                    Check in
                  </Button>
                </div>
                <StatusNote text={door} />
                {(data?.attendees ?? []).length > 0 && role === "organizer" && (
                  <div className="grid gap-2">
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
                )}
                <StatusNote text={attMsg} />
                <Button size="sm" variant="secondary" onClick={exportCsv}>
                  Export attendees CSV
                </Button>
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </main>
  );
}
