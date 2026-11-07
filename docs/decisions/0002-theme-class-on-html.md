# 0002 — Theme class on `<html>`

## Context
SPEC §12 requires light and dark themes. Tailwind's `dark` variant is class-based (`@custom-variant dark (&:is(.dark *))`), which matches elements *inside* `.dark`, not the element carrying the class. If M3 put the class on `<body>` or a wrapper, page-level styles and `color-scheme` would miss it, and a theme read after hydration would flash the wrong theme.

## Decision
- The theme class `.dark` lives on `<html>`. Light is the default (no class).
- `tokens.css` sets `color-scheme: light` on `:root` and `color-scheme: dark` on `.dark`, so native controls and scrollbars follow.
- M3 adds a small inline script in `<head>` that sets the class **before first paint** (stored preference, falling back to `prefers-color-scheme`), plus the toggle.
- `<html>` has `suppressHydrationWarning`, because that script changes its class before React hydrates.

## Consequences
- No theme flash; one place to read the active theme.
- The pre-paint script is the only inline script we allow; a future CSP must permit it (nonce or hash).
