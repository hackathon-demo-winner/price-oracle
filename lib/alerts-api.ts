import type { User } from 'firebase/auth';

// A real Firebase user can prove who they are to the backend; the demo fallback user cannot,
// so their alerts stay in this browser only.
export async function authHeaders(user: User | null): Promise<Record<string, string>> {
  if (!user || typeof user.getIdToken !== 'function') return {};
  return { Authorization: `Bearer ${await user.getIdToken()}` };
}

// The signed-in user's alerts from the database, or null when they cannot be loaded
export async function fetchMyAlerts(user: User | null): Promise<any[] | null> {
  const headers = await authHeaders(user);
  if (!headers.Authorization) return null;

  try {
    const res = await fetch('/api/my-alerts', { headers });
    if (!res.ok) return null;
    return (await res.json()).alerts || [];
  } catch {
    return null;
  }
}

export async function changeMyAlert(
  user: User | null,
  change:
    | { action: 'update'; alertId: string; targetPrice?: number | null; status?: 'active' | 'paused'; productTitle?: string }
    | { action: 'delete'; alertId: string }
): Promise<boolean> {
  const headers = await authHeaders(user);
  if (!headers.Authorization) return false;

  try {
    const res = await fetch('/api/my-alerts', {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify(change)
    });
    return res.ok;
  } catch {
    return false;
  }
}
