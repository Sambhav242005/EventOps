"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { DashboardPageNav } from "@/components/dashboard-page-nav";
import { loadSession, MyEvent, setEventId as saveSessionEventId } from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Member = { id: number; name: string; role: "member" | "organizer"; team_id: number | null };

export default function ManageWorkspacePage() {
  const [token, setToken] = useState("");
  const [role, setRole] = useState("");
  const [activeEventId, setActiveEventId] = useState(0);
  const [origin, setOrigin] = useState("");
  const [events, setEvents] = useState<MyEvent[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [name, setName] = useState("");
  const [when, setWhen] = useState("");
  const [venue, setVenue] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const authFetch = useCallback((path: string, init: RequestInit = {}) => fetch(`${API}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...init.headers },
  }), [token]);

  const loadData = useCallback(async () => {
    if (!token) return;
    try {
      const eventRes = await authFetch("/api/myevents");
      if (!eventRes.ok) throw new Error("Could not load your events.");
      const eventList = (await eventRes.json()) as MyEvent[];
      setEvents(eventList);
      if (eventList.length === 0) {
        setMembers([]);
        setNotice(role === "organizer" ? "Create your first event to get started." : "No events are available for this account yet.");
        return;
      }
      const selectedId = eventList.some(event => event.id === activeEventId)
        ? activeEventId
        : eventList[0].id;
      if (selectedId !== activeEventId) setActiveEventId(selectedId);
      const memberRes = await authFetch(`/api/members?event_id=${selectedId}`);
      if (memberRes.ok) setMembers(await memberRes.json());
      else if (memberRes.status === 403) setNotice("Your events are ready. Only organizers can view or change member roles.");
      else setNotice("Could not load members. Please retry.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Could not reach the server. Please retry."); }
  }, [activeEventId, authFetch, role, token]);

  useEffect(() => {
    const session = loadSession();
    if (!session) return;
    setToken(session.token);
    setRole(session.role);
    setActiveEventId(session.event_id ?? 0);
    setOrigin(window.location.origin);
  }, []);
  useEffect(() => { void loadData(); }, [loadData]);

  async function createEvent(e: FormEvent) {
    e.preventDefault();
    if (busy || name.trim().length < 2 || !when) return;
    setBusy(true); setNotice("");
    try {
      const response = await authFetch(`/api/events?event_id=${activeEventId}`, {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), date_time: new Date(when).toISOString(), venue }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not create event");
      setActiveEventId(result.event_id);
      saveSessionEventId(result.event_id);
      setName(""); setWhen(""); setVenue("");
      setNotice("Event created. Its guest check-in link is ready below.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Could not create event"); }
    finally { setBusy(false); }
  }

  async function changeRole(member: Member, nextRole: string) {
    try {
      const response = await authFetch(`/api/members/${member.id}?event_id=${activeEventId}`, {
        method: "PATCH", body: JSON.stringify({ role: nextRole }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not update role");
      setNotice(`${member.name} is now ${nextRole}.`);
      await loadData();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Could not update role"); }
  }

  function openEvent(id: number) {
    saveSessionEventId(id);
    window.location.href = "/dashboard";
  }

  return (
    <main className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
      <DashboardPageNav active="manage" title="Event & team management" description="Create events, share guest check-in, and assign organizer access." />
      <div>
        <h1 className="font-display text-2xl font-bold">Manage events and access</h1>
        <p className="mt-1 text-sm text-muted-foreground">Create events, share a unique entrance check-in QR, and set member roles.</p>
      </div>
      {notice && <p role="status" className="rounded-xl border bg-card px-4 py-3 text-sm">{notice}</p>}
      {role === "organizer" && events.length === 0 && <p className="rounded-xl border border-primary/20 bg-primary/5 px-4 py-3 text-sm">Your organization is ready. Create your first event to unlock its dashboard, guest QR, and team tools.</p>}
      {role !== "organizer" && <p className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">You’re signed in as a member. Ask an organizer to create events or update your role.</p>}
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Create an event</CardTitle><p className="text-sm text-muted-foreground">Events belong to your organization and will appear in the event switcher.</p></CardHeader>
          <CardContent>
            <form className="space-y-3" onSubmit={createEvent}>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder="Event name" aria-label="Event name" minLength={2} required disabled={role !== "organizer"} />
              <Input type="datetime-local" value={when} onChange={e => setWhen(e.target.value)} aria-label="Event date and time" required disabled={role !== "organizer"} />
              <Input value={venue} onChange={e => setVenue(e.target.value)} placeholder="Venue (optional)" aria-label="Venue" disabled={role !== "organizer"} />
              <Button type="submit" disabled={busy || role !== "organizer"}>{busy ? "Creating…" : "Create event"}</Button>
            </form>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Guest check-in links</CardTitle><p className="text-sm text-muted-foreground">Each event gets its own QR and link. Guests scan at the entrance, enter their details, and attendance is recorded.</p></CardHeader>
          <CardContent className="space-y-3">
            {(origin.includes("localhost") || origin.includes("127.0.0.1")) && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">Localhost links only work on this computer. Before sharing the QR with phones, open the app using a network-accessible or deployed URL.</p>}
            {events.map(event => {
              const link = `${origin}/join?event_id=${event.id}&door=1`;
              return <div key={event.id} className="flex items-center gap-3 rounded-xl border p-3">
                {origin && <img src={`${API}/api/event-qr/${event.id}?origin=${encodeURIComponent(origin)}`} alt={`Check-in QR for ${event.name}`} width={84} height={84} className="h-20 w-20 rounded bg-white" />}
                <div className="min-w-0 flex-1"><p className="truncate font-semibold">{event.name}</p><p className="text-xs text-muted-foreground">{event.venue || "Venue not set"} · {event.date_time ? new Date(event.date_time).toLocaleDateString() : "Date not set"}</p><Link href={link} className="break-all text-xs text-primary hover:underline">{link}</Link></div>
                <div className="flex shrink-0 flex-col gap-1"><Button size="sm" variant="secondary" onClick={() => navigator.clipboard.writeText(link)}>Copy</Button><Button size="sm" variant="ghost" onClick={() => openEvent(event.id)}>Open</Button></div>
              </div>;
            })}
            {events.length === 0 && <p className="rounded-xl bg-muted p-4 text-sm text-muted-foreground">No events are available for this account yet.</p>}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle>Member permissions</CardTitle><p className="text-sm text-muted-foreground">Organizer can manage events and approvals. Members can coordinate and perform standard event tasks. Role changes apply across the organization.</p></CardHeader>
        <CardContent className="space-y-2">
          {members.map(member => <div key={member.id} className="flex items-center justify-between gap-3 rounded-xl border px-3 py-2"><div className="min-w-0"><p className="truncate font-medium">{member.name}</p><p className="text-xs text-muted-foreground">Team {member.team_id ?? "—"}</p></div><select aria-label={`Role for ${member.name}`} value={member.role} disabled={role !== "organizer"} onChange={e => void changeRole(member, e.target.value)} className="h-9 rounded-full border bg-background px-3 text-sm"><option value="member">Member</option><option value="organizer">Organizer</option></select></div>)}
          {role === "organizer" && members.length === 0 && <p className="text-sm text-muted-foreground">No members found.</p>}
        </CardContent>
      </Card>
    </main>
  );
}
