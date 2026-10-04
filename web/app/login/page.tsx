"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { saveSession, saveMyEvents, setEventId, type MyEvent } from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type TeamOption = { team: string; team_id?: number; member_id?: number; role: string };
type LoginResp = {
  ok?: boolean;
  token?: string;
  name?: string;
  role?: string;
  error?: string;
  need_team?: boolean;
  options?: TeamOption[];
};

async function fetchMyEvents(token: string): Promise<MyEvent[] | null> {
  // Returns null when the backend does not support /api/myevents yet (404).
  try {
    const r = await fetch(`${API}/api/myevents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (r.status === 404) return null;
    if (!r.ok) return null;
    const data = (await r.json()) as MyEvent[] | { events?: MyEvent[] };
    if (Array.isArray(data)) return data;
    if (data && Array.isArray((data as { events?: MyEvent[] }).events)) {
      return (data as { events: MyEvent[] }).events;
    }
    return [];
  } catch {
    return null;
  }
}

export default function Login() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [pass, setPass] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [teamOptions, setTeamOptions] = useState<TeamOption[] | null>(null);

  async function doLogin(team?: string) {
    if (busy || !name.trim() || !pass) return;
    setBusy(true);
    setMsg("");
    try {
      const r = (await fetch(`${API}/api/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(team ? { name, passcode: pass, member_id: team } : { name, passcode: pass }),
      }).then((x) => x.json())) as LoginResp;
      if (r.need_team && Array.isArray(r.options)) {
        setTeamOptions(r.options);
        setMsg(
          r.options.length > 0
            ? "This name belongs to several teams — pick one to continue."
            : "This name needs a team choice, but the server sent no options."
        );
        return;
      }
      if (!r.token) {
        setMsg(`Login failed: ${r.error ?? "unknown error"}`);
        return;
      }
      const sessionName = r.name ?? name;
      const sessionRole = r.role ?? "";
      // Persist the session first so helpers have a token to work with.
      saveSession({ token: r.token, name: sessionName, role: sessionRole, event_id: 1 });
      // Best-effort: pull the user's event list; fall back to single-event mode.
      const events = await fetchMyEvents(r.token);
      if (events === null || events.length === 0) {
        saveMyEvents([]);
        setEventId(1);
        saveSession({ token: r.token, name: sessionName, role: sessionRole, event_id: 1 });
      } else {
        saveMyEvents(events);
        const firstId = Number(events[0]?.id) || 1;
        setEventId(firstId);
        saveSession({ token: r.token, name: sessionName, role: sessionRole, event_id: firstId });
      }
      setTeamOptions(null);
      router.replace("/dashboard");
    } catch {
      setMsg("Login failed: could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  function backToCredentials() {
    setTeamOptions(null);
    setMsg("");
  }

  return (
    <main className="mx-auto w-full max-w-md space-y-4 p-5 pt-16">
      <div className="text-center">
        <Link href="/" className="font-display text-xl font-extrabold">
          EventOps Agent
        </Link>
      </div>
      {teamOptions ? (
        <Card>
          <CardHeader>
            <CardTitle>Pick your team</CardTitle>
            <p className="text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{name}</span> is on more than one
              team. Choose which one to open.
            </p>
          </CardHeader>
          <CardContent className="space-y-2">
            {msg && (
              <p role="status" className="rounded-xl bg-muted px-3 py-2 text-sm text-muted-foreground">
                {msg}
              </p>
            )}
            {teamOptions.length === 0 && (
              <p role="alert" className="rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">
                No team options came back from the server. Go back and try again.
              </p>
            )}
            {teamOptions.map((o) => (
              <Button
                key={`${o.member_id ?? o.team_id ?? o.team}-${o.role}`}
                variant="secondary"
                disabled={busy}
                onClick={() => doLogin(String(o.member_id ?? o.team_id ?? o.team))}
                className="w-full justify-between"
              >
                <span className="truncate">{o.team}</span>
                <Badge variant="muted">{o.role}</Badge>
              </Button>
            ))}
            <Button variant="ghost" onClick={backToCredentials} disabled={busy} className="w-full">
              Back
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Log in</CardTitle>
            <p className="text-sm text-muted-foreground">
              Team access only — vendors and guests never log in here.
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <label htmlFor="login-name" className="text-sm font-medium">
                Name
              </label>
              <Input
                id="login-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doLogin()}
                placeholder="e.g. Asha"
                autoComplete="username"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="login-pass" className="text-sm font-medium">
                Passcode
              </label>
              <Input
                id="login-pass"
                value={pass}
                onChange={(e) => setPass(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doLogin()}
                placeholder="Passcode"
                type="password"
                autoComplete="current-password"
              />
            </div>
            {msg && (
              <p role="alert" className="rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">
                {msg}
              </p>
            )}
            <Button onClick={() => doLogin()} disabled={busy || !name.trim() || !pass} className="w-full">
              {busy ? "Joining…" : "Login"}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              New to the team?{" "}
              <Link href="/register" className="text-primary hover:underline">
                Register
              </Link>
            </p>
          </CardContent>
        </Card>
      )}
    </main>
  );
}
