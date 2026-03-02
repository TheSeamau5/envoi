# Fix FOUC on right panel open/close state

## Problem

`usePersistedState` reads localStorage inside `useState(() => ...)`. During SSR, `window` is undefined so it returns the default value (`rightPanelOpen=true`, `dividerPct=42`). During client hydration, `window` exists so it reads localStorage — if the user previously closed the panel, the client gets `rightPanelOpen=false` and renders `width: "100%"`, mismatching the server's `width: "42%"`. This causes a hydration mismatch and visible layout flash.

## Solution: Cookie-based persistence for layout-critical state

Use cookies instead of localStorage for the two layout-critical values (`rightPanelOpen`, `dividerPct`). The server can read cookies via `next/headers`, render the correct layout from the first paint, and the client hydration matches exactly. Zero flash.

### Step 1: Create a cookie read utility (`packages/web/src/lib/cookies.ts`)

- Export a `readLayoutCookies()` async function that uses `cookies()` from `next/headers`
- Returns `{ rightPanelOpen: boolean, dividerPct: number }` with sensible defaults
- Cookie names: `envoi:detail-right-panel-open`, `envoi:detail-divider-pct`

### Step 2: Update `TrajectoryDetail` to accept initial layout props

- Add `initialRightPanelOpen: boolean` and `initialDividerPct: number` to `TrajectoryDetailProps`
- Use these as the initial values for the two `useState` calls (replace `usePersistedState` for these two values)
- On state change, write to cookies via `document.cookie` (client-side) so the server can read them on next navigation
- Keep writing to localStorage too as a fallback (optional, but harmless)

### Step 3: Update the page component (`app/trajectory/[id]/page.tsx`)

- Import and call `readLayoutCookies()` (it's a server component, so it can use `cookies()`)
- Pass the values as props to `<TrajectoryDetail>`

### Step 4: Update `usePersistedState` to be hydration-safe (defense in depth)

- Change the `useState` initializer to **always** return `defaultValue` regardless of `window`
- Move the localStorage read into a `useEffect` that runs after hydration
- This makes the hook safe for any future consumer, not just this fix

## Files changed

1. **`packages/web/src/lib/cookies.ts`** — new, small server utility (~15 lines)
2. **`packages/web/src/components/trajectory/trajectory-detail.tsx`** — accept initial props, write cookies on change
3. **`packages/web/src/app/trajectory/[id]/page.tsx`** — read cookies, pass as props
4. **`packages/web/src/lib/storage.ts`** — fix hydration safety of `usePersistedState`
