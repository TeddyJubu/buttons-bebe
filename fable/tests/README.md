# Fable test suite

One command proves the whole help desk works **and** that it can never send
anything to a real customer / store / the internet:

```bash
./fable/scripts/test.sh            # unit + integration (with coverage)
FABLE_E2E=1 ./fable/scripts/test.sh   # also boots the real 4-service stack
```

Implements `fable/docs/TESTING-STRATEGY.md`.

## Layout

```
tests/
  conftest.py          fixtures: server on sys.path, in-process emulators, tmp DB, httpx router
  pytest.ini           markers + warning filters (rootdir anchor)
  unit/                risk, MockBrain, gorgias-compat mappers, cursors, config
  integration/         intake, pipeline, actions, tickets API, chat, stats,
                       shopify/redo/gorgias contracts, safety invariants, route coverage
  e2e/                 test_live_stack.py — @pytest.mark.e2e, skipped unless FABLE_E2E=1
```

## How the in-process wiring works (read this before touching conftest)

The suite runs **without opening a single real socket** for the unit and
integration layers. Three seams make that work:

1. **Server import path.** `fable/server` is inserted on `sys.path`, so tests do
   `import main` and `from app import ...` exactly like the server. The server
   reads `FABLE_DB` *live* (`config.db_path()`), so each test just points it at a
   fresh tempfile under `/tmp` (local disk — SQLite WAL refuses network fs).

2. **Emulators imported in-process.** All three emulators are files named
   `app.py`; importing them normally would clash with the server's `app`
   package. `conftest._load_emulator` uses `importlib` to load each under a
   unique module name (`fable_emu_shopify` …) and wraps its FastAPI app in a
   Starlette `TestClient`.

3. **httpx router (the key seam).** The server reaches the emulators via plain
   module-level `httpx.get` / `httpx.post` against `http://127.0.0.1:96xx`
   (see `app/context.py` and `app/actions.py`). The `env` fixture monkeypatches
   those two functions with a router that dispatches **by port** to the matching
   emulator `TestClient`. Because Fable's route handlers are sync (`def`), FastAPI
   runs them in a threadpool, so the nested `TestClient` call (e.g. Send →
   mailbox) is made from a plain worker thread and is safe.

   To simulate an emulator being down, `env.kill(9601)` adds the port to a `down`
   set and the router raises `httpx.ConnectError` — which `context.py` catches,
   degrading `order_context` to `null` while still drafting. `env.revive(port)`
   restores it.

### Determinism

The pipeline is a worker thread polling a `jobs` table. Tests call
`pipeline._run_once(conn)` (via `env.run_pipeline()`) directly instead of
sleeping, so drafting is deterministic. Two tests (`test_pipeline.py`,
`test_intake.py`) exercise the **real** thread with a short bounded poll to prove
it works end-to-end. `MockBrain` is deterministic (same context → same draft) and
emulator state is reset (`POST /emulator/reset`) at the start of every `env`.

## E2E (`FABLE_E2E=1`)

`e2e/test_live_stack.py` boots the real four services via the `scripts/run-*.sh`
launchers (with `FABLE_DB` on `/tmp`), runs the demo scenario from
API-CONTRACT §7 (Emma's `#BB1015` draft must contain tracking
`1Z999AA10123456784`), verifies the mailbox outbox, and checks kill-emulator
resilience. Everything binds `127.0.0.1`; services are torn down at the end.
