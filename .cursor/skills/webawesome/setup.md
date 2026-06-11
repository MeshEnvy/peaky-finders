# Web Awesome — project setup for Cursor

## Install dependency

```bash
npm install @awesome.me/webawesome
```

Optional Pro:

```bash
npm install @awesome.me/webawesome-pro
```

## Wire bundled docs (recommended)

After install, the authoritative skill lives at:

```
node_modules/@awesome.me/webawesome/dist/skills/webawesome/
```

This project skill (`.cursor/skills/webawesome/SKILL.md`) delegates to that directory. No copy or symlink required — the agent reads it directly when present.

### Optional: Cursor Docs index

Cursor Settings → Features → Docs → add:

- `node_modules/@awesome.me/webawesome/dist/skills/webawesome/` (progressive, preferred)
- or `node_modules/@awesome.me/webawesome/dist/llms.txt` (single-file fallback)

## Verify install

```bash
test -f node_modules/@awesome.me/webawesome/dist/skills/webawesome/SKILL.md && echo OK
```

## Keep docs in sync

Bundled skill and `llms.txt` update with the package version. Bump `@awesome.me/webawesome` in `package.json` to refresh AI context — no manual skill maintenance in this repo.
