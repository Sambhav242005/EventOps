"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DashboardPageNav } from "@/components/dashboard-page-nav";
import { getEventId, loadSession } from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const PAGE_SIZE = 50;
type Filter = "all" | "pending" | "approved" | "history";
type Notice = { id: number; recipient: string; body: string; status: string; send_at?: string; created_at?: string; approved_by?: string };
type PageData = { items: Notice[]; total: number; limit: number; offset: number };
const filters: { id: Filter; label: string }[] = [
  { id: "pending", label: "Needs approval" },
  { id: "approved", label: "Scheduled" },
  { id: "history", label: "History" },
  { id: "all", label: "All notices" },
];

function statusVariant(status: string): "success" | "warning" | "danger" | "muted" | "default" {
  if (status === "pending") return "warning";
  if (status === "approved" || status === "sending") return "default";
  if (status === "sent" || status === "delivered" || status === "read") return "success";
  if (status === "failed") return "danger";
  return "muted";
}

export default function SchedulePage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [eventId, setEventId] = useState(1);
  const [filter, setFilter] = useState<Filter>("pending");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<PageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [editBody, setEditBody] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (authToken: string, eid: number, currentFilter: Filter, currentPage: number) => {
    setLoading(true);
    setError("");
    try {
      const r = await fetch(`${API}/api/schedule?event_id=${eid}&status=${currentFilter}&limit=${PAGE_SIZE}&offset=${currentPage * PAGE_SIZE}`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (r.status === 401) {
        router.replace("/login");
        return;
      }
      const body = await r.json();
      if (!r.ok) throw new Error(body.error ?? `Could not load schedule (${r.status}).`);
      setData(body as PageData);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load schedule.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    const session = loadSession();
    if (!session) {
      router.replace("/login");
      return;
    }
    const eid = getEventId();
    setToken(session.token);
    setEventId(eid);
    void load(session.token, eid, "pending", 0);
  }, [load, router]);

  async function action(notice: Notice, kind: "approve" | "cancel" | "save") {
    setBusyId(notice.id);
    setError("");
    try {
      const path = kind === "approve" ? "/api/approve" : kind === "cancel" ? "/api/cancel_outbound" : "/api/edit_outbound";
      const body = kind === "save" ? { id: notice.id, body: editBody } : { id: notice.id };
      const r = await fetch(`${API}${path}?event_id=${eventId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(body),
      });
      const result = await r.json();
      if (!r.ok || result.ok === false) throw new Error(result.error ?? "That action could not be completed.");
      setEditing(null);
      setEditBody("");
      await load(token, eventId, filter, page);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That action could not be completed.");
    } finally {
      setBusyId(null);
    }
  }

  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const items = data?.items ?? [];

  return (
    <main className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
      <DashboardPageNav active="schedule" title="Notice schedule" description="Review drafts, approve delivery, and manage the five-minute edit window." />
      <div aria-label="Filter notices" className="flex flex-wrap gap-2 border-b pb-3">
        {filters.map((item) => (
          <button key={item.id} type="button" aria-pressed={filter === item.id}
            onClick={() => { setFilter(item.id); setPage(0); void load(token, eventId, item.id, 0); }}
            className={`rounded-full px-4 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${filter === item.id ? "bg-primary font-semibold text-primary-foreground" : "bg-muted text-muted-foreground hover:text-foreground"}`}>
            {item.label}
          </button>
        ))}
        <span className="ms-auto self-center text-sm text-muted-foreground">{loading ? "Loading…" : `${total} notice${total === 1 ? "" : "s"}`}</span>
      </div>
      {error && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"><span>{error}</span><Button size="sm" variant="secondary" onClick={() => load(token, eventId, filter, page)}>Try again</Button></div>}
      {!loading && !error && items.length === 0 && (
        <Card><CardContent className="py-12 text-center">
          <h2 className="font-semibold">{filter === "pending" ? "No notices need approval" : "No notices in this view"}</h2>
          <p className="mt-1 text-sm text-muted-foreground">Draft audience notices from the event room with @agent or the notice controls.</p>
        </CardContent></Card>
      )}
      <div className="space-y-3">
        {items.map((notice) => (
          <Card key={notice.id}><CardContent className="space-y-3 py-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Notice #{notice.id}</h2><Badge variant="muted">{notice.recipient.replace(/^audience:/, "") || "Recipient unavailable"}</Badge><Badge variant={statusVariant(notice.status)}>{notice.status}</Badge></div>
              <time className="text-xs text-muted-foreground" dateTime={notice.send_at || notice.created_at}>{notice.send_at ? `Send time: ${new Date(notice.send_at).toLocaleString()}` : notice.created_at ? new Date(notice.created_at).toLocaleString() : ""}</time>
            </div>
            {editing === notice.id ? (
              <div className="space-y-2">
                <label className="sr-only" htmlFor={`notice-body-${notice.id}`}>Edit notice text</label>
                <textarea id={`notice-body-${notice.id}`} value={editBody} onChange={(e) => setEditBody(e.target.value)} rows={4} maxLength={1500} className="w-full resize-y rounded-xl border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                <div className="flex flex-wrap gap-2"><Button size="sm" disabled={busyId === notice.id || !editBody.trim()} onClick={() => action(notice, "save")}>{busyId === notice.id ? "Saving…" : "Save changes"}</Button><Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Discard</Button></div>
              </div>
            ) : <p className="whitespace-pre-wrap break-words text-sm leading-6">{notice.body}</p>}
            {(notice.status === "pending" || notice.status === "approved") && editing !== notice.id && (
              <div className="flex flex-wrap gap-2 border-t pt-3">
                {notice.status === "pending" && <Button size="sm" disabled={busyId === notice.id} onClick={() => action(notice, "approve")}>Approve notice</Button>}
                <Button size="sm" variant="secondary" disabled={busyId === notice.id} onClick={() => { setEditing(notice.id); setEditBody(notice.body); }}>Edit</Button>
                <Button size="sm" variant="ghost" disabled={busyId === notice.id} onClick={() => action(notice, "cancel")}>Cancel</Button>
              </div>
            )}
          </CardContent></Card>
        ))}
      </div>
      {total > PAGE_SIZE && <div className="flex items-center justify-between border-t pt-4"><Button variant="ghost" disabled={page === 0 || loading} onClick={() => { const next = page - 1; setPage(next); void load(token, eventId, filter, next); }}>Previous</Button><span className="text-sm text-muted-foreground">Page {page + 1} of {pages}</span><Button variant="ghost" disabled={page + 1 >= pages || loading} onClick={() => { const next = page + 1; setPage(next); void load(token, eventId, filter, next); }}>Next</Button></div>}
    </main>
  );
}
