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
type Alert = { id: number; severity: string; kind: string; text: string; created_at: string; acknowledged: number };
type AlertPage = { items: Alert[]; total: number; limit: number; offset: number };

function severityVariant(severity: string): "danger" | "warning" | "muted" {
  if (severity === "high" || severity === "critical") return "danger";
  if (severity === "medium") return "warning";
  return "muted";
}

export default function AlertsPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [eventId, setEventId] = useState(1);
  const [page, setPage] = useState(0);
  const [data, setData] = useState<AlertPage | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (authToken: string, eid: number, currentPage: number) => {
    setLoading(true);
    setError("");
    try {
      const r = await fetch(`${API}/api/alerts?event_id=${eid}&limit=${PAGE_SIZE}&offset=${currentPage * PAGE_SIZE}`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (r.status === 401) {
        router.replace("/login");
        return;
      }
      const body = await r.json();
      if (!r.ok) throw new Error(body.error ?? `Could not load alerts (${r.status}).`);
      setData(body as AlertPage);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load alerts.");
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
    void load(session.token, eid, 0);
  }, [load, router]);

  async function acknowledge(alertId: number) {
    setBusyId(alertId);
    try {
      const r = await fetch(`${API}/api/alerts/ack?event_id=${eventId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ id: alertId }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.error ?? "Could not acknowledge this alert.");
      await load(token, eventId, page);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not acknowledge this alert.");
    } finally {
      setBusyId(null);
    }
  }

  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const items = data?.items ?? [];

  return (
    <main className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
      <DashboardPageNav active="alerts" title="Event alerts" description="Weather, vendor and schedule risks for the selected event." />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{loading ? "Loading alerts…" : `${total} alert${total === 1 ? "" : "s"}`}</p>
        <Button variant="secondary" size="sm" onClick={() => load(token, eventId, page)} disabled={loading || !token}>
          {loading ? "Refreshing…" : "Refresh"}
        </Button>
      </div>
      {error && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          <span>{error}</span>
          <Button size="sm" variant="secondary" onClick={() => load(token, eventId, page)}>Try again</Button>
        </div>
      )}
      {!loading && !error && items.length === 0 && (
        <Card><CardContent className="py-12 text-center">
          <h2 className="font-semibold">No alerts for this event</h2>
          <p className="mt-1 text-sm text-muted-foreground">New weather checks and event risks will appear here.</p>
        </CardContent></Card>
      )}
      <div className="space-y-2">
        {items.map((alert) => (
          <Card key={alert.id}>
            <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={severityVariant(alert.severity)}>{alert.severity}</Badge>
                  <span className="text-sm font-medium">{alert.kind}</span>
                  {alert.acknowledged ? <Badge variant="muted">Acknowledged</Badge> : <Badge variant="warning">Open</Badge>}
                </div>
                <p className="break-words text-sm leading-6">{alert.text}</p>
                <time className="block text-xs text-muted-foreground" dateTime={alert.created_at}>
                  {alert.created_at ? new Date(alert.created_at).toLocaleString() : "Time unavailable"}
                </time>
              </div>
              {!alert.acknowledged && (
                <Button size="sm" variant="secondary" disabled={busyId === alert.id} onClick={() => acknowledge(alert.id)}>
                  {busyId === alert.id ? "Saving…" : "Acknowledge"}
                </Button>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between border-t pt-4">
          <Button variant="ghost" disabled={page === 0 || loading} onClick={() => { const next = page - 1; setPage(next); void load(token, eventId, next); }}>Previous</Button>
          <span className="text-sm text-muted-foreground">Page {page + 1} of {pages}</span>
          <Button variant="ghost" disabled={page + 1 >= pages || loading} onClick={() => { const next = page + 1; setPage(next); void load(token, eventId, next); }}>Next</Button>
        </div>
      )}
    </main>
  );
}
