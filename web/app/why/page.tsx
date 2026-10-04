import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const REASONS = [
  {
    title: "Vendors don't install apps",
    body: "Your caterer lives on WhatsApp and phone calls, not Slack. EventOps meets vendors where they are and brings their replies back into your room as structured data — available, price, conditions.",
  },
  {
    title: "The critical hours are chaotic",
    body: "At T-5 hours nobody has time to re-message 30 people one by one. One approved notice reaches the whole team or all guests, with a buffer to catch your own typos first.",
  },
  {
    title: "AI proposes, humans dispose",
    body: "The agent drafts, researches, and calls — but bookings, money, and mass sends always wait for an organizer's tap. Every decision is logged and auditable.",
  },
  {
    title: "Open-weight at the core",
    body: "Reasoning, vision, and extraction run on open-weight Gemma 4 (via Ollama locally or the Gemini API). Messaging and voice providers are swappable adapters, not lock-in.",
  },
  {
    title: "Built for real doors",
    body: "Guests scan one event QR, enter their details, and are checked in immediately. Organizers get duplicate detection and CSV exports.",
  },
];

export default function Why() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 p-5 sm:p-8">
      <header className="flex items-center gap-3">
        <Link href="/" className="font-display text-xl font-extrabold">
          EventOps Agent
        </Link>
        <nav className="ms-auto flex items-center gap-4 text-sm">
          <Link href="/features" className="text-muted-foreground hover:text-foreground">
            Features
          </Link>
          <Link href="/login">
            <Button size="sm">Log in</Button>
          </Link>
        </nav>
      </header>

      <div className="space-y-2">
        <h1 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">
          Why use it
        </h1>
        <p className="text-muted-foreground">
          Five reasons teams pick EventOps over another spreadsheet and a prayer.
        </p>
      </div>

      <div className="space-y-4">
        {REASONS.map((r, i) => (
          <Card key={r.title}>
            <CardHeader>
              <CardTitle>
                <span className="text-muted-foreground">{i + 1}. </span>
                {r.title}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">{r.body}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="flex justify-center pb-6">
        <Link href="/register">
          <Button size="lg">Join your team</Button>
        </Link>
      </div>
    </main>
  );
}
