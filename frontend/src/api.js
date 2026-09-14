const BASE = import.meta.env.VITE_API_BASE || "";

export async function getHealth() {
  const r = await fetch(`${BASE}/api/health`);
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export async function uploadDocument(sessionId, file) {
  const fd = new FormData();
  fd.append("session_id", sessionId);
  fd.append("file", file);
  const r = await fetch(`${BASE}/api/upload`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`upload ${r.status}`);
  return r.json();
}

export async function listRuns(limit = 25) {
  const r = await fetch(`${BASE}/api/runs?limit=${limit}`);
  if (!r.ok) throw new Error(`runs ${r.status}`);
  return r.json();
}

/**
 * Stream a research run. Returns an abort function.
 * onStage(label, payload) fires per pipeline step; onDone(result) at the end.
 */
export function streamResearch(query, { sessionId = "", onStage, onDone, onError }) {
  const params = new URLSearchParams({ q: query });
  if (sessionId) params.set("session_id", sessionId);

  const es = new EventSource(`${BASE}/api/research/stream?${params}`);

  const parse = (e) => {
    try {
      return JSON.parse(e.data);
    } catch {
      return { raw: e.data };
    }
  };

  // Named events emitted by the backend pipeline.
  ["plan", "research", "draft", "decompose", "verify", "score", "stage"].forEach((name) =>
    es.addEventListener(name, (e) => onStage?.(name, parse(e)))
  );

  es.addEventListener("done", (e) => {
    onDone?.(parse(e));
    es.close();
  });

  es.addEventListener("error", (e) => {
    onError?.(e);
    es.close();
  });

  // Fallback for unnamed messages.
  es.onmessage = (e) => {
    const d = parse(e);
    if (d.answer !== undefined) {
      onDone?.(d);
      es.close();
    } else {
      onStage?.(d.step || "stage", d);
    }
  };

  return () => es.close();
}
