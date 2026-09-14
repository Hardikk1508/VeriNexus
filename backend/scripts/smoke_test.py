"""End-to-end smoke test against a running server.

    uvicorn app.main:app --reload        # terminal 1
    python scripts/smoke_test.py         # terminal 2

Checks health, then streams a research run and prints each stage as it lands.
Exits non-zero on failure so it can be wired into CI later.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request

DEFAULT_QUERY = "Does retrieval-augmented generation reduce hallucination in large language models?"


def get_json(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=30) as r:
        return json.loads(r.read())


def check_health(base):
    print("→ GET /api/health")
    h = get_json(base, "/api/health")
    print(f"   status         : {h.get('status')}")
    print(f"   model          : {h.get('model')}")
    print(f"   nli_backend    : {h.get('nli_backend')}")
    print(f"   audit persisted: {h.get('audit_persistence')}")
    missing = h.get("missing_config") or []
    if missing:
        print(f"   ⚠  missing config: {', '.join(missing)}")
    if "GROQ_API_KEY" in missing:
        print("\n   GROQ_API_KEY is not set — /api/research will refuse. Stopping here.")
        return False
    return h.get("status") == "ok"


def stream_research(base, query, timeout):
    qs = urllib.parse.urlencode({"q": query})
    url = f"{base}/api/research/stream?{qs}"
    print(f"\n→ GET /api/research/stream?q={query[:50]}...\n")

    event, seen = None, 0
    with urllib.request.urlopen(url, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                seen += 1
                payload = line.split(":", 1)[1].strip()
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = {"raw": payload}
                render(event, data)
    return seen


def render(event, data):
    if event == "done" or "answer" in data:
        print("\n" + "=" * 68)
        print(f"CONFIDENCE : {data.get('confidence')}   BAND: {data.get('band')}")
        print(f"REVIEW     : {data.get('needs_human_review')}")
        if data.get("flags"):
            print(f"FLAGS      : {', '.join(data['flags'])}")
        print("=" * 68)
        print((data.get("answer") or "")[:1200])
        claims = data.get("claims") or []
        if claims:
            print(f"\nCLAIMS ({len(claims)}):")
            for c in claims:
                print(f"  [{c.get('band','?'):<12}] {c.get('confidence'):<6} "
                      f"{'CONFLICT ' if c.get('conflicted') else ''}{c.get('text','')[:80]}")
    else:
        label = data.get("label") or data.get("step") or event
        ms = data.get("duration_ms")
        print(f"   · {label}{f'  ({ms} ms)' if ms else ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--query", default=DEFAULT_QUERY)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    try:
        if not check_health(args.base):
            sys.exit(1)
        n = stream_research(args.base, args.query, args.timeout)
        print(f"\n✓ smoke test finished — {n} SSE events received")
    except urllib.error.URLError as e:
        print(f"\n✗ cannot reach {args.base} — is uvicorn running?\n  {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"\n✗ smoke test failed: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
