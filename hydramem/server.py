"""The mem0 method surface over HTTP, on `http.server`.

ponytail: stdlib, not FastAPI. The project has five dependencies and none of
them is a web framework; six endpoints that take a JSON body and return a JSON
body do not justify a seventh dependency plus an ASGI server. Swap it when
something here needs concurrency, auth or a schema layer -- none of which this
surface has, deliberately, because it is a demo and evaluation surface rather
than a product boundary.

    python -m hydramem.server            # 127.0.0.1:8800

    POST /add      {"messages": [...], "user_id": "u1"}   -> {..., "bookmark"}
    POST /search   {"query": "...", "user_id": "u1", "bookmark": "..."}
    GET  /get_all?user_id=u1
    GET  /history?user_id=u1&memory_id=...
    POST /delete   {"memory_id": "...", "user_id": "u1"}
    POST /explain  {"query": "...", "user_id": "u1"}
    GET  /                                            -> the single-page demo UI

The bookmark is a real query parameter on `/search`, not an internal detail:
demonstrating read-your-own-writes over the wire is the point of the endpoint
existing at all.
"""

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .memory import Memory

HOST, PORT = "127.0.0.1", 8800

# One Memory per process. It holds a driver and the last bookmark; the driver is
# thread-safe and the bookmark is advisory, so sharing it is what makes
# "add then search" work across two HTTP calls from the same client.
_memory = None


def memory() -> Memory:
    global _memory
    if _memory is None:
        _memory = Memory()
    return _memory


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def _query(self) -> dict:
        parsed = urllib.parse.urlparse(self.path)
        return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    def _send_page(self):
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        route = urllib.parse.urlparse(self.path).path
        args = self._query()
        if route == "/":
            return self._send_page()
        try:
            if route == "/get_all":
                return self._send(memory().get_all(user_id=args.get("user_id")))
            if route == "/history":
                return self._send(memory().history(
                    memory_id=args.get("memory_id"), user_id=args.get("user_id")))
            if route == "/healthz":
                return self._send({"ok": True})
        except Exception as exc:  # noqa: BLE001
            return self._send({"error": str(exc)}, 500)
        self._send({"error": "not found", "path": route}, 404)

    def do_POST(self):  # noqa: N802
        route = urllib.parse.urlparse(self.path).path
        try:
            body = self._body()
        except json.JSONDecodeError as exc:
            return self._send({"error": f"bad json: {exc}"}, 400)
        try:
            if route == "/add":
                return self._send(memory().add(
                    body.get("messages", []), user_id=body.get("user_id"),
                    timestamp=body.get("timestamp")))
            if route == "/search":
                return self._send(memory().search(
                    body.get("query", ""), user_id=body.get("user_id"),
                    bookmarks=body.get("bookmark")))
            if route == "/delete":
                return self._send(memory().delete(
                    body.get("memory_id", ""), user_id=body.get("user_id")))
            if route == "/explain":
                return self._send({"explain": memory().explain(
                    body.get("query", ""), user_id=body.get("user_id"))})
        except Exception as exc:  # noqa: BLE001
            return self._send({"error": str(exc)}, 500)
        self._send({"error": "not found", "path": route}, 404)

    def log_message(self, *_args):
        pass          # the default logger writes to stderr on every request


def serve(host: str = HOST, port: int = PORT):
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"hydramem mem0-compatible API on http://{host}:{port}")
    server.serve_forever()


# ponytail: one inlined string, no build step, no framework, no asset pipeline.
# Issue 16 holds the UI to a hard four-hour timebox and calls it a demo surface
# rather than the product, so it shows the four things the submission argues --
# the answer or the abstention *reason*, the gate trace that produced it, the
# facts it rests on, and the supersession chain -- and nothing else.
PAGE = """<!doctype html>
<meta charset="utf-8"><title>HydraMem</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#111; --mut:#666;
          --line:#d8d8d8; --card:#fafafa; --ok:#0a7d3f; --no:#a4432b; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14161a; --fg:#e8e8e8; --mut:#9aa0a6; --line:#2c3038;
            --card:#1b1e24; --ok:#5fd08a; --no:#e08a6f; }
  }
  * { box-sizing: border-box; }
  body { margin:0; padding:2rem 1rem; background:var(--bg); color:var(--fg);
         font:15px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif; }
  main { max-width: 54rem; margin: 0 auto; }
  h1 { font-size:1.4rem; margin:0 0 .25rem; }
  p.sub { color:var(--mut); margin:0 0 1.5rem; }
  form { display:flex; gap:.5rem; flex-wrap:wrap; }
  input, button { font:inherit; padding:.6rem .7rem; border:1px solid var(--line);
                  border-radius:.4rem; background:var(--bg); color:var(--fg); }
  input[name=q] { flex:1 1 22rem; }
  input[name=u] { flex:0 1 12rem; }
  button { cursor:pointer; font-weight:600; }
  section { margin-top:1.5rem; border:1px solid var(--line);
            border-radius:.5rem; background:var(--card); padding:1rem; }
  h2 { font-size:.75rem; text-transform:uppercase; letter-spacing:.08em;
       color:var(--mut); margin:0 0 .6rem; }
  .verdict { font-size:1.15rem; font-weight:650; }
  .ok { color:var(--ok); } .no { color:var(--no); }
  code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
              font-size:.85em; }
  pre { margin:0; white-space:pre-wrap; word-break:break-word; }
  .row { border-top:1px solid var(--line); padding:.55rem 0; }
  .row:first-of-type { border-top:0; }
  .mut { color:var(--mut); }
  .chain { display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; }
  .was { text-decoration: line-through; color:var(--mut); }
  .hide { display:none; }
</style>
<main>
  <h1>HydraMem</h1>
  <p class="sub">Ask a tenant a question. Abstention is a result, and it names
    its reason.</p>

  <form id="f">
    <input name="q" placeholder="How many bikes do I currently own?" required>
    <input name="u" placeholder="user_id / instance_id" value="89941a93">
    <button>Ask</button>
  </form>

  <section id="out" class="hide">
    <h2>Result</h2>
    <div class="verdict" id="verdict"></div>
    <div class="mut" id="cost"></div>
  </section>

  <section id="trace-s" class="hide"><h2>Gate trace</h2><pre id="trace"></pre></section>
  <section id="ev-s" class="hide"><h2>Facts it rests on</h2><div id="ev"></div></section>
  <section id="hist-s" class="hide"><h2>Supersession chain</h2><div id="hist"></div></section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const show = (id, on) => $(id).classList.toggle('hide', !on);
const esc = (s) => String(s ?? '').replace(/[&<>]/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

$('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = $('f').q.value, u = $('f').u.value;
  $('verdict').textContent = 'asking...'; show('out', true);
  show('trace-s', false); show('ev-s', false); show('hist-s', false);

  const r = await fetch('/search', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query: q, user_id: u})
  }).then(x => x.json());

  if (r.error) { $('verdict').innerHTML = '<span class="no">' + esc(r.error) + '</span>'; return; }

  $('verdict').innerHTML = r.abstained
    ? '<span class="no">ABSTAIN</span> &mdash; <code>' + esc(r.reason) + '</code>'
    : '<span class="ok">' + esc(r.answer) + '</span>';
  $('cost').textContent = r.round_trips + ' Bolt round trips';

  if (r.gate_trace?.length) {
    $('trace').textContent = r.gate_trace.join('\n'); show('trace-s', true);
  }
  if (r.results?.length) {
    $('ev').innerHTML = r.results.map(f =>
      '<div class="row"><code>' + esc(f.id) + '</code> &middot; ' + esc(f.memory) +
      ' <span class="mut">(' + esc(f.role) + ')</span>' +
      (f.snippet ? '<div class="mut">&ldquo;' + esc(f.snippet) + '&rdquo;</div>' : '') +
      '</div>').join(''); show('ev-s', true);
  }

  const h = await fetch('/history?user_id=' + encodeURIComponent(u)).then(x => x.json());
  if (h.results?.length) {
    $('hist').innerHTML = h.results.map(c =>
      '<div class="row chain"><code>' + esc(c.predicate) + '</code>' +
      '<span class="was">' + esc(c.old_memory) + '</span> &rarr; <b>' +
      esc(c.new_memory) + '</b></div>').join(''); show('hist-s', true);
  }
});
</script>
"""


if __name__ == "__main__":
    serve()
