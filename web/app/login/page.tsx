"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { saveSession } from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Login() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [pass, setPass] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (busy || !name.trim() || !pass) return;
    setBusy(true);
    setMsg("");
    try {
      const r = await fetch(`${API}/api/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, passcode: pass }),
      }).then((x) => x.json());
      if (!r.ok || !r.token) {
        setMsg(`Login failed: ${r.error ?? "unknown error"}`);
        return;
      }
      saveSession({ token: r.token, name: r.name ?? name, role: r.role ?? "" });
      router.replace("/dashboard");
    } catch {
      setMsg("Login failed: could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-md space-y-4 p-5 pt-16">
      <div className="text-center">
        <Link href="/" className="font-display text-xl font-extrabold">
          EventOps Agent
        </Link>
      </div>
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
              onKeyDown={(e) => e.key === "Enter" && submit()}
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
              onKeyDown={(e) => e.key === "Enter" && submit()}
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
          <Button onClick={submit} disabled={busy || !name.trim() || !pass} className="w-full">
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
    </main>
  );
}
