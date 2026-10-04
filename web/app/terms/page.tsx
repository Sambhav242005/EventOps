import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const SECTIONS = [
  {
    title: "1. What this is",
    body: "EventOps Agent is an open-source (MIT) event-coordination tool built for a student hackathon demo. It is provided as-is, without warranty of any kind.",
  },
  {
    title: "2. Your content",
    body: "Event names, vendor details, attendee lists, messages, and call transcripts you enter are stored in the local database of whoever runs the server. The hosted demo keeps data only to operate the service; do not enter passwords, bank details, or other sensitive personal data.",
  },
  {
    title: "3. Messaging and calls",
    body: "WhatsApp messages go through Twilio/WhatsApp's network and phone calls through ElevenLabs/Twilio — their terms and carrier rates apply. Every mass send and every call requires explicit organizer approval inside the app, plus a 5-minute buffer. Approved content is your responsibility.",
  },
  {
    title: "4. AI-generated content",
    body: "Drafts, vendor rankings, transcriptions, and photo extractions are AI-assisted and can be wrong. High-confidence matches still deserve a human glance; low-confidence items are always queued for review, never auto-confirmed.",
  },
  {
    title: "5. Accounts",
    body: "Team accounts use short demo passcodes, not passwords. Anyone with your passcode can act as you, including approving sends. Organizer accounts must be created by the server operator, never through self-registration (which only creates member accounts).",
  },
  {
    title: "6. Acceptable use",
    body: "No spam, no harassment, no unlawful surveillance. Vendor and guest contact details collected for an event must only be used for that event. Respect opt-outs (reply STOP) — the app enforces them automatically.",
  },
];

export default function Terms() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 p-5 sm:p-8">
      <header className="flex items-center gap-3">
        <Link href="/" className="font-display text-xl font-extrabold">
          EventOps Agent
        </Link>
        <nav className="ms-auto flex items-center gap-4 text-sm">
          <Link href="/about" className="text-muted-foreground hover:text-foreground">
            About
          </Link>
          <Link href="/login">
            <Button size="sm">Log in</Button>
          </Link>
        </nav>
      </header>

      <div className="space-y-2">
        <h1 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">Terms</h1>
        <p className="text-sm text-muted-foreground">
          Short, plain version. Last updated October 2026.
        </p>
      </div>

      <div className="space-y-4">
        {SECTIONS.map((s) => (
          <Card key={s.title}>
            <CardHeader>
              <CardTitle>{s.title}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">{s.body}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <p className="pb-6 text-center text-sm text-muted-foreground">
        Questions? Ask in the team room — or open an issue on the repo.
      </p>
    </main>
  );
}
