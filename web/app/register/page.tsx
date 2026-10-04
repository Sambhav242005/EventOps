"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { saveMyEvents, saveSession, setEventId } from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Register() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [pass, setPass] = useState("");
  const [role, setRole] = useState<"member" | "organizer">("member");
  const [organizationName, setOrganizationName] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (busy || name.trim().length < 2 || pass.length < 4 ||
        (role === "organizer" && organizationName.trim().length < 2)) return;
    setBusy(true);
    setMsg("");
    try {
      const r = await fetch(`${API}/api/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, passcode: pass, role,
          ...(role === "organizer" ? { organization_name: organizationName.trim() } : {}) }),
      }).then((x) => x.json());
      if (!r.ok) {
        setMsg(`Registration failed: ${r.error ?? "unknown error"}`);
        return;
      }
      const l = await fetch(`${API}/api/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, passcode: pass }),
      }).then((x) => x.json());
      if (l?.need_team) {
        // Same name exists on several teams — let the login screen resolve it.
        router.replace("/login");
        return;
      }
      saveSession({ token: l.token, name: l.name ?? name, role: l.role ?? "member", event_id: 1 });
      // Best-effort event list; missing endpoint => single-event mode (event_id=1).
      try {
        const me = await fetch(`${API}/api/myevents`, {
          headers: { Authorization: `Bearer ${l.token}` },
        });
        if (me.ok) {
          const data = (await me.json()) as
            | import("@/lib/session").MyEvent[]
            | { events?: import("@/lib/session").MyEvent[] };
          const list = Array.isArray(data) ? data : (data.events ?? []);
          saveMyEvents(list);
          if (list.length === 0 && l.role === "organizer") {
            setEventId(0);
            saveSession({ token: l.token, name: l.name ?? name, role: l.role, event_id: 0 });
            router.replace("/dashboard/manage");
            return;
          }
          const firstId = Number(list[0]?.id) || 1;
          setEventId(firstId);
          saveSession({
            token: l.token,
            name: l.name ?? name,
            role: l.role ?? "member",
            event_id: firstId,
          });
        } else {
          saveMyEvents([]);
          setEventId(1);
        }
      } catch {
        saveMyEvents([]);
      }
      router.replace("/dashboard");
    } catch {
      setMsg("Registration failed: could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  const valid = name.trim().length >= 2 && pass.length >= 4 &&
    (role !== "organizer" || organizationName.trim().length >= 2);

  return (
    <main className="mx-auto w-full max-w-md space-y-4 p-5 pt-16">
      <div className="text-center">
        <Link href="/" className="font-display text-xl font-extrabold">
          EventOps Agent
        </Link>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Join or create a workspace</CardTitle>
          <p className="text-sm text-muted-foreground">
              Create a team-member account, or set up an organization that starts with no events.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Account type</legend>
            <label className="flex cursor-pointer items-start gap-3 rounded-xl border bg-background p-3">
              <input type="radio" name="account-role" value="member" checked={role === "member"} onChange={() => setRole("member")} className="mt-1 accent-primary" />
              <span><span className="block text-sm font-semibold">Team member</span><span className="block text-xs text-muted-foreground">Join the event team with standard access.</span></span>
            </label>
            <label className="flex cursor-pointer items-start gap-3 rounded-xl border bg-background p-3">
              <input type="radio" name="account-role" value="organizer" checked={role === "organizer"} onChange={() => setRole("organizer")} className="mt-1 accent-primary" />
              <span><span className="block text-sm font-semibold">Create an organization</span><span className="block text-xs text-muted-foreground">You’ll create its first event after signing in.</span></span>
            </label>
          </fieldset>
          {role === "organizer" && (
            <div className="space-y-1.5">
              <label htmlFor="reg-org-name" className="text-sm font-medium">Organization name</label>
              <Input id="reg-org-name" value={organizationName} onChange={e => setOrganizationName(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()} placeholder="e.g. Indore Events" autoComplete="organization" />
            </div>
          )}
          <div className="space-y-1.5">
            <label htmlFor="reg-name" className="text-sm font-medium">
              Name
            </label>
            <Input
              id="reg-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder="Your name"
              autoComplete="username"
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="reg-pass" className="text-sm font-medium">
              Choose a passcode (4+ characters)
            </label>
            <Input
              id="reg-pass"
              value={pass}
              onChange={(e) => setPass(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder="Passcode"
              type="password"
              autoComplete="new-password"
            />
          </div>
          {msg && (
            <p role="alert" className="rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {msg}
            </p>
          )}
          <Button onClick={submit} disabled={busy || !valid} className="w-full">
            {busy ? "Creating…" : "Register"}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            Already on the team?{" "}
            <Link href="/login" className="text-primary hover:underline">
              Log in
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
