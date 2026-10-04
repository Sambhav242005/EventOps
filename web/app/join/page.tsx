"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Join() {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [qr, setQr] = useState("");
  const [who, setWho] = useState("");
  const [checkedIn, setCheckedIn] = useState(false);
  const [doorMode, setDoorMode] = useState(false);

  useEffect(() => {
    setDoorMode(new URLSearchParams(window.location.search).get("door") === "1");
  }, []);

  async function submit() {
    if (busy || name.trim().length < 2 || phone.trim().length < 7) return;
    setBusy(true);
    setMsg("");
    try {
      const eventId = new URLSearchParams(window.location.search).get("event_id");
      const params = new URLSearchParams();
      if (eventId) params.set("event_id", eventId);
      const doorMode = new URLSearchParams(window.location.search).get("door") === "1";
      if (doorMode) params.set("door", "1");
      const r = await fetch(`${API}/api/register_attendee?${params.toString()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, phone }),
      }).then((x) => x.json());
      if (doorMode && r.checked_in) {
        setCheckedIn(true);
        setWho(r.name ?? name);
        setMsg(r.duplicate ? "You were already registered. Your attendance is now recorded." : "Your attendance has been recorded.");
        return;
      }
      if (!r.qr_token) {
        setMsg(`Registration failed: ${r.error ?? "unknown error"}`);
        return;
      }
      setQr(r.qr_token);
      setWho(r.name ?? name);
      setMsg(
        r.duplicate
          ? "This number is already registered — here is your existing QR."
          : "Registered! Screenshot this QR and show it at the door."
      );
    } catch {
      setMsg("Registration failed: could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  const valid = name.trim().length >= 2 && phone.trim().length >= 7;

  return (
    <main className="mx-auto w-full max-w-md space-y-4 p-5 pt-10">
      <div className="text-center">
        <Link href="/" className="font-display text-xl font-extrabold">
          EventOps Agent
        </Link>
        <p className="mt-1 text-sm text-muted-foreground">{doorMode ? "Event door check-in" : "Participant registration"}</p>
      </div>
      {checkedIn ? (
        <Card>
          <CardContent className="space-y-2 p-6 text-center">
            <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-emerald-100 text-2xl text-emerald-700" aria-hidden="true">✓</div>
            <h1 className="font-display text-xl font-bold">You’re checked in, {who}!</h1>
            <p className="text-sm text-muted-foreground">{msg}</p>
          </CardContent>
        </Card>
      ) : !qr ? (
        <Card>
          <CardHeader>
            <CardTitle>{doorMode ? "Check in to the event" : "Register as a participant"}</CardTitle>
            <p className="text-sm text-muted-foreground">
              {doorMode
                ? "Enter your name and phone. Your attendance will be recorded right away — no app or login needed."
                : "Enter your name and WhatsApp-capable number. You get a QR for the door — no app or login needed."}
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <label htmlFor="join-name" className="text-sm font-medium">
                Full name
              </label>
              <Input
                id="join-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submit()}
                placeholder="Your name"
                autoComplete="name"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="join-phone" className="text-sm font-medium">
                Phone (with country code)
              </label>
              <Input
                id="join-phone"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submit()}
                placeholder="+91…"
                inputMode="tel"
                autoComplete="tel"
              />
            </div>
            {msg && (
              <p role="alert" className="rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">
                {msg}
              </p>
            )}
            <Button onClick={submit} disabled={busy || !valid} className="w-full">
              {busy ? (doorMode ? "Checking in…" : "Registering…") : (doorMode ? "Check in now" : "Get my door QR")}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>You are in, {who}!</CardTitle>
            <p className="text-sm text-muted-foreground">{msg}</p>
          </CardHeader>
          <CardContent className="space-y-3 text-center">
            <img
              src={`${API}/api/qr/${encodeURIComponent(qr)}`}
              alt={`Check-in QR for ${who}`}
              width={240}
              height={240}
              className="mx-auto rounded-2xl border"
            />
            <p className="font-mono text-sm">{qr}</p>
            <Badge variant="success">Show this at the gate</Badge>
            <div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setQr("");
                  setMsg("");
                }}
              >
                Register someone else
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </main>
  );
}
