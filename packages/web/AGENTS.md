# AGENTS.md — Envoi Dashboard

This file contains all rules, conventions, and architectural context for AI agents working on this codebase.

## Stack

- **Framework**: Next.js 15 (App Router)
- **Package manager**: pnpm (always pnpm, never npm or yarn)
- **Language**: TypeScript (strict)
- **Styling**: Tailwind CSS v4 (CSS-first config in globals.css)
- **Components**: shadcn/ui primitives, extended as needed
- **Icons**: lucide-react (no emoji, no custom SVGs)
- **Charts**: Custom SVG (no chart libraries)
- **State**: React state + URL params (no external state management)
- **Font**: JetBrains Mono only — everything is monospace

## TypeScript Rules

1. `undefined` over `null` — never use `null`. Convert at boundaries if needed.
2. `useState<T>()` — not `useState<T | undefined>()`.
3. `type` over `interface` — always. No `interface` keyword anywhere.
4. `const` over `let` — only `let` when reassignment is genuinely needed. Never `var`.
5. No single-letter variable names — `index` not `i`, `suite` not `s`. Exception: `x`/`y` in chart math.
6. No classes — use functions, closures, plain objects.

## React & Next.js

7. Server components first — every `page.tsx` is a server component. Only interactive parts use `"use client"`.
8. shadcn/ui for UI primitives — buttons, tabs, selects, tables, tooltips.
9. lucide-react for all icons.

## Styling

10. Padding over margins — use padding and gap. Only acceptable margin: `margin: 0 auto`.
11. Design tokens in `src/lib/tokens.ts` for inline SVG styles.
12. Tailwind theme tokens in `src/app/globals.css`.

## Code Quality

13. JSDoc comments on every exported function, component, and type.
14. Zero TypeScript errors, zero warnings, zero ESLint errors.
15. The build must be clean: `pnpm build` exits 0 with no warnings.

## Architecture

- `src/lib/` — shared utilities, types, constants, mock data generation
- `src/components/` — all React components
- `src/app/` — Next.js App Router pages
- Mock data is generated server-side (pure, deterministic functions) and passed to client components as props
- `Set<number>` is not serializable across server/client — use `number[]` for FileSnapshot.added

## Design Language

- Monospace everything (JetBrains Mono)
- Orange accent (#f97316) for active/selected states
- White + light gray backgrounds, no dark mode
- ALL CAPS section headers (10px, letter-spacing 0.06em)
- Information-dense, power-user focused
- Bottom status bar with green dot

## localStorage & UI Persistence

16. Use `usePersistedState` from `src/lib/storage.ts` for all localStorage access — never call `localStorage` directly.
17. All keys are auto-prefixed with `envoi:`. Use descriptive kebab-case key names (e.g., `"sidebar-collapsed"`, `"divider-position"`).
18. The hook handles SSR, invalid JSON, schema changes, and quota errors. Always provide a sensible default value.
19. Never crash on bad/missing/outdated localStorage data — always fall back to defaults.
20. Validate stored values against current valid options before using them.
