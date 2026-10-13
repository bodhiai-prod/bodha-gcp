# Frontend

## Stack and application shell

The frontend is a React 19 SPA built with CRA/CRACO, Tailwind, Radix-based UI primitives, Axios, React Router, Firebase Web SDK, KaTeX, and Recharts. `App.js` composes browser routing, Firebase-backed `AuthProvider`, confirmation state, and the toast host.

## Route access model

| Route group | Guard / audience |
| --- | --- |
| `/login`, `/register`, `/forgot-password`, `/reset-password` | Public Firebase authentication journeys. |
| `/complete-profile` | Authenticated user, profile completion intentionally allowed. |
| `/`, `/textbooks`, `/papers*`, `/qbank`, `/settings/branding`, `/competitive-exams*`, `/account` | Authenticated workspace; teacher landing behavior redirects students appropriately. |
| `/student*` | `StudentRoute`; student dashboard, new mock test, attempt, and result. |
| `/admin` | `AdminRoute`; application admin only. |

## Identity flow

`AuthContext` listens to Firebase auth state. After sign-in or registration it posts user/profile details to `/auth/sync`, stores the returned application user, and clears temporary onboarding data once the profile is complete. Password reset uses Firebase email actions returning to `/reset-password`. Logout signs out of Firebase and clears application user state.

`src/lib/api.js` creates an Axios instance under `${REACT_APP_BACKEND_URL}/api`, asynchronously adds the current Firebase token to each request, and redirects browser sessions to `/login` following a 401 response. The production Cloud Build passes an empty backend URL so this resolves to same-origin `/api` through Caddy.

## UI organization and status behavior

Teacher pages use shared paper-studio components for configuration, allocation previews, status badges, insights, and saved configurations. `PaperView` and solution views poll persisted API state; status presentation must tolerate queued, generating, reviewing, ready, needs-review, failure, and partial/recovery states. New status fields should be backward-compatible with older paper records and stale poll responses must not overwrite newer state.

## Adding a route

Create the page under the appropriate `pages/` directory, add the route in `App.js`, choose the narrowest existing guard, call the shared Axios client rather than raw unauthenticated requests, and write focused component/helper tests. Keep an API authorization check on the server; a route guard is user experience, not access control.
