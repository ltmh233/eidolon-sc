// Run after npm run build. Requires EIDOLON_CORE_BINARY and the repository's core packs.
// No external services, credentials, published ports, or databases are used.
import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = fileURLToPath(new URL("..", import.meta.url));
const root = resolve(frontend, "..");
const binary = process.env.EIDOLON_CORE_BINARY;
assert(binary, "Set EIDOLON_CORE_BINARY to a built Go core executable");
const limit = 2000; // Same failure window as the original acceptance, far above local RTT.
const rounds = Number(process.env.SSE_ROUNDS ?? 3);
const states = new Map();
const sockets = new Set();
let serial = 0;
let failures = 0;
let passed = 0;
const deferred = () => Promise.withResolvers();
async function within(promise, label, ms = limit) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label}: exceeded ${ms}ms`)), ms);
    })]);
  } finally { clearTimeout(timer); }
}
const upstream = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://test");
  const state = states.get(url.searchParams.get("id"));
  if (state) {
    state.res = res;
    state.req = req;
    res.on("finish", () => { state.finished = true; });
    res.on("close", () => {
      state.closedAt = performance.now();
      state.closed.resolve(state.closedAt);
    });
    // FastAPI reads the JSON request before returning SSE. Do likewise: emitting
    // response headers during an unfinished POST tests HTTP/1 full duplex instead.
    // The separate upload case verifies forwarding before the upload is complete.
    if (state.mode !== "upload") for await (const _chunk of req) { /* consume */ }
    state.arrived.resolve();
    if (state.mode === "headers") return;
    if (state.mode === "upload") {
      req.once("data", () => state.first.resolve(performance.now()));
      let body = "";
      for await (const chunk of req) body += chunk;
      res.end(body);
      return;
    }
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store" });
    state.written = performance.now();
    res.write("data: first\n\n");
    return; // The test controls completion: no timer can make a buffered proxy pass.
  }
  if (url.pathname === "/api/auth/github/start") {
    res.writeHead(302, { location: "https://example.invalid/oauth?state=test",
      "set-cookie": ["oauth=test; HttpOnly; SameSite=Lax", "second=test; HttpOnly"] });
    res.end();
    return;
  }
  let body = "";
  for await (const chunk of req) body += chunk;
  res.writeHead(Number(url.searchParams.get("status") ?? 200), { "content-type": "application/json" });
  res.end(JSON.stringify({ cookie: req.headers.cookie, origin: req.headers.origin,
    scope: req.headers["x-work-id"], body }));
});
upstream.on("connection", socket => {
  sockets.add(socket);
  socket.on("close", () => sockets.delete(socket));
});
await new Promise(resolve => upstream.listen(0, "127.0.0.1", resolve));
const upstreamURL = `http://127.0.0.1:${upstream.address().port}`;
const core = spawn(binary, ["-listen", "127.0.0.1:0", "-legacy", upstreamURL,
  "-packs", `${root}/core/packs,${root}/core/testdata/packs`]);
const ready = deferred();
core.stderr.on("data", data => {
  process.stderr.write(data);
  const match = data.toString().match(/catalog listening on (\S+)/);
  if (match) ready.resolve(`http://${match[1]}`);
});
core.on("error", ready.reject);
const coreURL = await within(ready.promise, "Go startup", 10000);

async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exit = once(child, "exit");
  child.kill("SIGTERM");
  try { await within(exit, "process exit", 5000); }
  catch { child.kill("SIGKILL"); await exit; }
}
async function check(name, fn) {
  try { await fn(); passed++; console.log(`PASS ${name}`); }
  catch (err) { failures++; console.log(`FAIL ${name}: ${err.message}`); }
}
function request(base, mode, method = "GET") {
  const id = String(++serial);
  const state = { mode, arrived: deferred(), closed: deferred(), first: deferred(), finished: false };
  states.set(id, state);
  const first = deferred();
  const ended = deferred();
  let text = "";
  const req = http.request(`${base}/api/accept?id=${id}`, { method, agent: false }, res => {
    res.on("data", chunk => { text += chunk; first.resolve(performance.now()); });
    res.on("end", () => ended.resolve({ text, complete: res.complete }));
    res.on("error", () => ended.resolve({ text, complete: false }));
    res.on("aborted", () => ended.resolve({ text, complete: false }));
  });
  req.on("error", () => ended.resolve({ text, complete: false }));
  if (mode !== "upload") req.end(method === "POST" ? "test-body" : undefined);
  return { state, first, ended, req, cleanup() {
    req.destroy();
    state.res?.destroy();
    states.delete(id);
  } };
}
try {
  for (const [topology, backend] of [["direct", upstreamURL], ["go", coreURL]]) {
    // Reserve an ephemeral port, then release it immediately before spawning Next.
    const reservation = http.createServer();
    await new Promise(resolve => reservation.listen(0, "127.0.0.1", resolve));
    const port = reservation.address().port;
    await new Promise(resolve => reservation.close(resolve));
    const next = spawn("node", [".next/standalone/server.js"], { cwd: frontend,
      env: { ...process.env, PORT: String(port), HOSTNAME: "127.0.0.1", BACKEND_URL: backend } });
    next.stdout.resume();
    next.stderr.on("data", data => process.stderr.write(data));
    const base = `http://127.0.0.1:${port}`;
    try {
      let ready = false;
      for (let i = 0; i < 100; i++) {
        try { if ((await fetch(`${base}/api/ready`, { signal: AbortSignal.timeout(500) })).ok) { ready = true; break; } } catch {}
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      assert(ready, "Next did not become ready");
      for (const method of ["GET", "POST"]) {
        await check(`${topology} ${method} normal SSE`, async () => {
          const q = request(base, "stream", method);
          try {
            const received = await within(q.first.promise, "first chunk");
            assert(!q.state.finished);
            q.state.res.end("data: second\n\nevent: done\ndata: {}\n\n");
            const result = await within(q.ended.promise, "normal completion");
            assert(result.complete);
            assert.equal(result.text, "data: first\n\ndata: second\n\nevent: done\ndata: {}\n\n");
            await within(q.state.closed.promise, "upstream normal close");
            assert(q.state.finished, "normal response was cancelled");
            console.log(`METRIC ${topology} ${method} first_ms=${(received - q.state.written).toFixed(3)}`);
          } finally { q.cleanup(); }
        });
        for (const phase of ["headers", "stream"]) for (const close of ["fin", "reset"]) {
          for (let round = 0; round < rounds; round++) {
            await check(`${topology} ${method} ${phase} ${close} ${round + 1}`, async () => {
              const q = request(base, phase, method);
              try {
                await within(phase === "headers" ? q.state.arrived.promise : q.first.promise, "request reached phase");
                assert.equal(q.state.closedAt, undefined, `upstream closed before client cancellation (finished=${q.state.finished}, requestComplete=${q.state.req.complete}, requestAborted=${q.state.req.aborted})`);
                const start = performance.now();
                if (close === "reset") q.req.socket.resetAndDestroy();
                else q.req.socket.end();
                const closed = await within(q.state.closed.promise, "upstream cancellation");
                assert(!q.state.finished, "upstream completed instead of being cancelled");
                console.log(`METRIC ${topology} ${method} ${phase} ${close} cancel_ms=${(closed - start).toFixed(3)}`);
              } finally { q.cleanup(); }
            });
          }
        }
      }
      await check(`${topology} upstream stream error`, async () => {
        const q = request(base, "stream");
        try {
          await within(q.first.promise, "first chunk");
          q.state.res.destroy();
          const result = await within(q.ended.promise, "downstream error");
          assert(!result.complete);
          assert.equal(result.text, "data: first\n\n");
        } finally { q.cleanup(); }
      });
      await check(`${topology} streamed upload`, async () => {
        const q = request(base, "upload", "POST");
        try {
          q.req.write("first");
          await within(q.state.first.promise, "upstream sees unfinished upload");
          q.req.end("second");
          assert.equal((await within(q.ended.promise, "upload completion")).text, "firstsecond");
        } finally { q.cleanup(); }
      });
      await check(`${topology} HTTP compatibility`, async () => {
        for (const status of [200, 401, 403, 404, 428, 500]) {
          const res = await fetch(`${base}/api/echo?status=${status}`, { method: "POST", headers: {
            cookie: "eidolon_session=test-only", origin: "http://localhost", "x-work-id": "sc",
          }, body: "test" });
          assert.equal(res.status, status);
          assert.deepEqual(await res.json(), { cookie: "eidolon_session=test-only", origin: "http://localhost", scope: "sc", body: "test" });
        }
        const res = await fetch(`${base}/api/auth/github/start`, { redirect: "manual" });
        assert.equal(res.status, 302);
        assert.equal(res.headers.get("location"), "https://example.invalid/oauth?state=test");
        assert.equal(res.headers.getSetCookie().length, 2);
        if (topology === "go") for (const header of [true, false]) {
          const res = await fetch(`${base}/api/conversations`, { method: "POST",
            headers: { "content-type": "application/json", ...(header ? { "x-work-id": "demo" } : {}) },
            body: JSON.stringify({ character_id: "mano", ...(header ? {} : { work_id: "demo" }) }) });
          assert.equal(res.status, 409);
          await res.arrayBuffer();
        }
      });
    } finally { await stop(next); }
  }
} finally {
  for (const socket of sockets) socket.destroy();
  await stop(core);
  await new Promise(resolve => upstream.close(resolve));
}
console.log(`RESULT passed=${passed} failed=${failures} rounds=${rounds} window_ms=${limit}`);
process.exitCode = failures ? 1 : 0;
