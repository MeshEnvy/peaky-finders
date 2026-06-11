---
name: webawesome
description: >-
  Web Awesome UI components (wa-* custom elements): buttons, inputs, dialogs,
  forms, tabs, theming, CSS utilities. Use when building or styling with Web
  Awesome, migrating from Shoelace, or referencing https://webawesome.com/docs.
---

# Web Awesome

[Web Awesome](https://webawesome.com/docs) is a framework-agnostic web component library (successor to Shoelace). Components use the `wa-` prefix, native form association, cascade layers, and OKLCH theming.

## Context source (prefer bundled skill)

Web Awesome ships machine-readable docs with every npm release. **Always prefer the bundled skill over guessing APIs.**

1. If `node_modules/@awesome.me/webawesome` exists, read:
   - `node_modules/@awesome.me/webawesome/dist/skills/webawesome/SKILL.md`
   - Then load only task-relevant files under `.../references/` (e.g. `references/components/dialog.md`, `references/frameworks/react.md`).
2. If not installed, install first:
   ```bash
   npm install @awesome.me/webawesome
   ```
   Then read the bundled skill path above.
3. Fallback when npm is unavailable: `node_modules/@awesome.me/webawesome/dist/llms.txt` (full API in one file) or live docs at [webawesome.com/docs](https://webawesome.com/docs).

Official AI docs: [Using with AI](https://webawesome.com/docs/ai) · [Agent Skills](https://webawesome.com/docs/ai/agent-skills) · [LLMs.txt](https://webawesome.com/docs/ai/llms)

## Quick start

```bash
npm install @awesome.me/webawesome
```

```js
import '@awesome.me/webawesome/dist/styles/webawesome.css'
import '@awesome.me/webawesome/dist/components/button/button.js'
```

CDN / hosted project: [webawesome.com](https://webawesome.com) (autoloader line). See bundled `references/installation.md` for npm, CDN, and self-host options.

## Conventions (do not invent APIs)

| Topic | Rule |
|-------|------|
| Elements | `wa-*` tags; **always use closing tags** (no self-closing custom elements) |
| Events | Prefixed `wa-*` (e.g. `wa-change`, `wa-after-show`) |
| CSS vars | `--wa-*` custom properties |
| Brand color | `variant="brand"` (not `primary`) |
| Button look | `appearance="filled" \| "outlined" \| "plain"` (not `outline` / `circle` / `text`) |
| Input slots | `start` / `end` (not `prefix` / `suffix`); hint slot (not `help-text`) |
| Forms | Native `FormData` / `checkValidity()` via `ElementInternals` |
| Theming | Classes on `<html>`: `wa-theme-*`, `wa-palette-*`, `wa-light` / `wa-dark` |
| Layers | Component styles in `@layer wa-component`; unlayered app CSS wins ties |
| JS readiness | `await customElements.whenDefined('wa-button')` or `allDefined()` from `webawesome.js` |
| Lit updates | After property changes, `await element.updateComplete` before reading reflected attrs |

## Shoelace migration (2.x → Web Awesome 3)

Package: `@shoelace-style/shoelace` → `@awesome.me/webawesome`. Prefixes: `sl-` → `wa-`, `--sl-*` → `--wa-*`, events `sl-*` → `wa-*`. Soft landing: `class="wa-theme-shoelace wa-palette-shoelace"` on `<html>`. Some components moved to **Pro** (toast, combobox, file input, charts). Full guide: [Migrating from Shoelace](https://webawesome.com/docs/resources/migrating-from-shoelace).

## Workflow

1. Identify components needed; read matching `references/components/<name>.md` from the bundled skill.
2. For framework integration, read `references/frameworks/<framework>.md` ([frameworks overview](https://webawesome.com/docs/frameworks)).
3. For forms, read bundled `references/form-controls.md` ([form controls](https://webawesome.com/docs/form-controls)).
4. For layout/theming, read `references/customizing.md`, `references/themes.md`, or utility docs under `references/utilities/`.
5. Verify properties, slots, events, and parts against the reference — **do not assume native HTML parity** (e.g. `wa-button` defaults `type` to `button`, not `submit`).

## Pro vs Free

Free: 50+ components. Pro (`@awesome.me/webawesome-pro`): extra layout/pattern components, premium themes, combobox, file input, toast, charts, etc. Do not use Pro APIs unless the project has Pro installed.

## Editor support

VS Code: add to `.vscode/settings.json`:

```json
{
  "html.customData": ["./node_modules/@awesome.me/webawesome/dist/vscode.html-custom-data.json"]
}
```

## Additional resources

- Live docs index: [webawesome.com/docs](https://webawesome.com/docs)
- Usage patterns: [Usage](https://webawesome.com/docs/usage)
- CSS utilities: [Utilities](https://webawesome.com/docs/utilities)
- Project setup checklist: [setup.md](setup.md)
