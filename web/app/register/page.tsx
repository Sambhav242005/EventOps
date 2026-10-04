"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { saveSession } from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Register() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [pass, setPass] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (busy || name.trim().length < 2 || pass.length < 4) return;
    setBusy(true);
    setMsg("");
    try {
      const r = await fetch(`${API}/api/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, passcode: pass }),
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
      saveSession({ token: l.token, name: l.name ?? name, role: l.role ?? "member" });
      router.replace("/dashboard");
    } catch {
      setMsg("Registration failed: could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  const valid = name.trim().length >= 2 && pass.length >= 4;

  return (
    <main className="mx-auto w-full max-w-md space-y-4 p-5 pt-16">
      <div className="text-center">
        <Link href="/" className="font-display text-xl font-extrabold">
          EventOps Agent
        </Link>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Join the team</CardTitle>
          <p className="text-sm text-muted-foreground">
            Creates a team-member account. Organizer-only actions stay gated.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
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
