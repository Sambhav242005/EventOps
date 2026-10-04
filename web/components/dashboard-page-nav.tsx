import Link from "next/link";

export function DashboardPageNav({
  title,
  description,
  active,
}: {
  title: string;
  description: string;
  active: "schedule" | "alerts" | "manage";
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 border-b pb-5">
      <div className="min-w-0">
        <Link href="/dashboard" className="text-sm font-semibold text-primary hover:underline">
          ← Event room
        </Link>
        <h1 className="mt-2 font-display text-2xl font-bold tracking-tight">{title}</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p>
      </div>
      <nav aria-label="Dashboard" className="flex items-center gap-1 rounded-xl bg-muted p-1 text-sm">
        <Link
          href="/dashboard/schedule"
          aria-current={active === "schedule" ? "page" : undefined}
          className={`rounded-lg px-3 py-2 ${active === "schedule" ? "bg-card font-semibold text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
        >
          Schedule
        </Link>
        <Link
          href="/dashboard/alerts"
          aria-current={active === "alerts" ? "page" : undefined}
          className={`rounded-lg px-3 py-2 ${active === "alerts" ? "bg-card font-semibold text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
        >
          Alerts
        </Link>
        <Link
          href="/dashboard/manage"
          aria-current={active === "manage" ? "page" : undefined}
          className={`rounded-lg px-3 py-2 ${active === "manage" ? "bg-card font-semibold text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
        >
          Manage
        </Link>
      </nav>
    </header>
  );
}
