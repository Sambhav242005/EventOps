import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const GROUPS: { title: string; items: { name: string; body: string; tag: string }[] }[] = [
  {
    title: "Coordinate",
    items: [
      {
        name: "Team room + @agent",
        body: "One shared room instead of 40 scattered chats. Tag @agent with a request in plain words — Hindi, English, or Hinglish.",
        tag: "Chat",
      },
      {
        name: "Approvals with buffer",
        body: "Routine answers go out instantly. Mass notices and calls wait for organizer approval — then sit in a 5-minute buffer where you can still edit or cancel.",
        tag: "Control",
      },
      {
        name: "Audience notices",
        body: "Team, guests, everyone, or one number directly. Preview the recipient count before anything sends, with delivery tracking after.",
        tag: "Broadcast",
      },
    ],
  },
  {
    title: "Reach",
    items: [
      {
        name: "WhatsApp to vendors",
        body: "Text the caterer, tent house, and sound crew where they already live. Replies are extracted into availability, price, and conditions automatically.",
        tag: "WhatsApp",
      },
      {
        name: "AI phone calls",
        body: "An AI voice agent calls vendors with a strict brief: ask availability, price, conditions — never book, never pay, never share your budget.",
        tag: "Voice",
      },
      {
        name: "Telegram fallback",
        body: "Same send/approve pipeline over Telegram when WhatsApp is down or a vendor prefers it.",
        tag: "Telegram",
      },
    ],
  },
  {
    title: "Survive the crisis",
    items: [
      {
        name: "Background watch",
        body: "A worker monitors weather, unconfirmed vendors, and the countdown clock (T-7d, T-24h, T-5h) and posts alerts into the room before you ask.",
        tag: "Proactive",
      },
      {
        name: "Ranked backups",
        body: "When a vendor cancels, replacements are scored on price, distance, availability, and freshness — with per-factor reasons, never black-box.",
        tag: "Ranking",
      },
      {
        name: "Door + reports",
        body: "One event QR lets guests enter their details and check in at the door, with duplicate detection and one-click CSV exports.",
        tag: "On-site",
      },
    ],
  },
];

export default function Features() {
  return (
    <main className="mx-auto max-w-5xl space-y-8 p-5 sm:p-8">
      <header className="flex items-center gap-3">
        <Link href="/" className="font-display text-xl font-extrabold">
          EventOps Agent
        </Link>
        <nav className="ms-auto flex items-center gap-4 text-sm">
          <Link href="/why" className="text-muted-foreground hover:text-foreground">
            Why use it
          </Link>
          <Link href="/about" className="text-muted-foreground hover:text-foreground">
            About
          </Link>
          <Link href="/login">
            <Button size="sm">Log in</Button>
          </Link>
        </nav>
      </header>

      <div className="space-y-2">
        <h1 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">
          Features
        </h1>
        <p className="max-w-xl text-muted-foreground">
          Everything the team needs between “the caterer cancelled” and “the show went on”.
        </p>
      </div>

      {GROUPS.map((g) => (
        <section key={g.title} className="space-y-3">
          <h2 className="font-display text-xl font-bold">{g.title}</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {g.items.map((f) => (
              <Card key={f.name}>
                <CardHeader>
                  <Badge variant="muted">{f.tag}</Badge>
                  <CardTitle>{f.name}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-muted-foreground">{f.body}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>
      ))}

      <div className="flex justify-center gap-2 pb-6">
        <Link href="/register">
          <Button size="lg">Try the dashboard</Button>
        </Link>
        <Link href="/terms">
          <Button size="lg" variant="secondary">
            Terms
          </Button>
        </Link>
      </div>
    </main>
  );
}
