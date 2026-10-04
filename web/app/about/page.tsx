import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function About() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 p-5 sm:p-8">
      <header className="flex items-center gap-3">
        <Link href="/" className="font-display text-xl font-extrabold">
          EventOps Agent
        </Link>
        <nav className="ms-auto flex items-center gap-4 text-sm">
          <Link href="/login" className="text-muted-foreground hover:text-foreground">
            Log in
          </Link>
          <Link href="/dashboard">
            <Button size="sm">Dashboard</Button>
          </Link>
        </nav>
      </header>

      <h1 className="font-display text-3xl font-extrabold tracking-tight">About</h1>

      <Card>
        <CardHeader>
          <CardTitle>The problem</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            College fests, local conferences, and community events run on scattered
            WhatsApp chats, phone calls, and spreadsheets. When a caterer cancels the
            day before, nobody has one view of who is confirmed, who to call next, or
            who needs to be told.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>How it works</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            <strong className="text-foreground">1. Chat.</strong> The team talks in
            the room and tags @agent.
          </p>
          <p>
            <strong className="text-foreground">2. Agent proposes.</strong> Gemma 4
            drafts notices, researches backup vendors, and briefs phone calls.
          </p>
          <p>
            <strong className="text-foreground">3. Humans approve.</strong> A
            rule-based decision layer gates risk: routine answers run free, mass
            notices and calls need organizer approval — with a 5-minute buffer to
            fix mistakes.
          </p>
          <p>
            <strong className="text-foreground">4. Everyone is reached.</strong>{" "}
            Vendors get WhatsApp and phone calls; guests get notices; the door runs
            QR plus photo check-in; everything exports to CSV.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Honest limits</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            Production WhatsApp needs a verified business account and approved
            templates. Web-found vendors are unverified leads until a human confirms
            them. Voice calls cost real money and need organizer approval every time.
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
