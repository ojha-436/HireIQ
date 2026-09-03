# scripts/

## `scan_secrets.py` — run this before every push

Gates the staged set against the **live** values in `backend/.env`, rather than guessing
at patterns, because the only values that matter are the ones this machine actually holds.

```bash
git add -A && python3 scripts/scan_secrets.py
```

It exits non-zero and names the file if anything sensitive is staged.

Config is deliberately **not** treated as a secret: a model name and a CORS origin list
are neither sensitive nor avoidable, and they belong in the committed defaults. Only keys
whose name marks them sensitive block a commit. `AGORA_APP_ID` warns rather than blocks —
it reaches the browser by design, so it is not a secret, but there is no reason to publish
it either.

This exists because the real keys were once pasted into `.env.example` — the committed
template — instead of `.env`. Nothing had been committed yet, but nothing was stopping it.
