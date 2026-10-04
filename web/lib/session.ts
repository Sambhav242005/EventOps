export type MyEvent = {
  id: number;
  name: string;
  date_time?: string;
  venue?: string;
  org_id?: number;
  team_id?: number | null;
};

export type Session = { token: string; name: string; role: string; event_id?: number };

const K = "eventops.session";
const EVENTS_K = "eventops.myevents";

export const DEFAULT_EVENT_ID = 1;

export function saveSession(s: Session) {
  try {
    localStorage.setItem(K, JSON.stringify(s));
  } catch {
    /* private mode */
  }
}

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(K);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return s.token ? s : null;
  } catch {
    return null;
  }
}

export function clearSession() {
  try {
    localStorage.removeItem(K);
  } catch {
    /* ignore */
  }
}

/** Current event id: session.event_id, falling back to DEFAULT_EVENT_ID. */
export function getEventId(): number {
  try {
    const raw = localStorage.getItem(K);
    if (!raw) return DEFAULT_EVENT_ID;
    const s = JSON.parse(raw) as Session;
    const id = Number(s.event_id);
    return Number.isFinite(id) && id > 0 ? id : DEFAULT_EVENT_ID;
  } catch {
    return DEFAULT_EVENT_ID;
  }
}

/** Persist the selected event id inside the stored session (no-op when logged out). */
export function setEventId(eventId: number) {
  try {
    const raw = localStorage.getItem(K);
    if (!raw) return;
    const s = JSON.parse(raw) as Session;
    if (!s.token) return;
    s.event_id = eventId;
    localStorage.setItem(K, JSON.stringify(s));
  } catch {
    /* ignore */
  }
}

export function saveMyEvents(events: MyEvent[]) {
  try {
    localStorage.setItem(EVENTS_K, JSON.stringify(events));
  } catch {
    /* private mode */
  }
}

export function loadMyEvents(): MyEvent[] {
  try {
    const raw = localStorage.getItem(EVENTS_K);
    if (!raw) return [];
    const list = JSON.parse(raw) as MyEvent[];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function clearMyEvents() {
  try {
    localStorage.removeItem(EVENTS_K);
  } catch {
    /* ignore */
  }
}
