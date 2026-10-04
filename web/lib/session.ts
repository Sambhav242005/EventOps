export type Session = { token: string; name: string; role: string };

const K = "eventops.session";

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
