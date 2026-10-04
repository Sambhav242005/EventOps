import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const FEATURES = [
  {
    title: "Team room + @agent",
    body: "Chat normally, tag @agent. It drafts notices, researches backups, and proposes calls — approvals stay with the organizer.",
  },
  {
    title: "Reach vendors anywhere",
    body: "WhatsApp and phone calls to people who never install your app. Replies land back in the room as structured updates.",
  },
  {
    title: "Crisis-ready",
    body: "Background watch flags cancelled vendors and bad weather early, with ranked backups attached. A 5-minute buffer lets you unsend mistakes.",
  },
  {
    title: "Door in seconds",
    body: "Guests scan one event QR, enter their details, and are checked in instantly. Export the final attendance list in one click.",
  },
];

export default function Home() {
  return (
    <main className="mx-auto max-w-5xl space-y-10 p-5 sm:p-8">
      <header className="flex items-center gap-3">
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
        <span className="font-display text-xl font-extrabold">EventOps Agent</span>
        <nav className="ms-auto flex items-center gap-4 text-sm">
          <Link href="/about" className="text-muted-foreground hover:text-foreground">
            About
          </Link>
          <Link href="/login" className="text-muted-foreground hover:text-foreground">
            Log in
          </Link>
          <Link href="/register">
            <Button size="sm">Join team</Button>
          </Link>
        </nav>
      </header>

      <section className="space-y-4 pt-6 text-center">
        <Badge>Open-weight · Gemma 4 · MIT</Badge>
        <h1 className="font-display text-4xl font-extrabold tracking-tight sm:text-5xl">
          Plan. Coordinate.
          <br />
          Handle the chaos.
        </h1>
        <p className="mx-auto max-w-xl text-muted-foreground">
          One shared room for fests, conferences, and community events — vendors on
          WhatsApp and phone, attendance at the door, and an AI agent that proposes
          while organizers dispose.
        </p>
        <div className="flex justify-center gap-2">
          <Link href="/register">
            <Button size="lg">Open the dashboard</Button>
          </Link>
          <Link href="/about">
            <Button size="lg" variant="secondary">
              How it works
            </Button>
          </Link>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        {FEATURES.map((f) => (
          <Card key={f.title}>
            <CardHeader>
              <CardTitle>{f.title}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">{f.body}</p>
            </CardContent>
          </Card>
        ))}
      </section>

      <footer className="pb-6 text-center text-sm text-muted-foreground">
        EventOps Agent · MIT licensed · team room lives at{" "}
        <Link href="/dashboard" className="text-primary hover:underline">
          /dashboard
        </Link>
      </footer>
    </main>
  );
}
