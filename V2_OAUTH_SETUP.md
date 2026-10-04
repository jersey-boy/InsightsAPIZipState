# NationBuilder V2 API + OAuth 2.0 — Setup & Reference

This document captures everything needed to talk to the **NationBuilder V2 API**
using **OAuth 2.0**: how OAuth works in general, how NationBuilder implements it,
exactly what was set up for this project, the confirmed V2 request/response
shapes for the fields we care about, and the token-refresh flow the scheduled job
will need.

It is written to be a standalone reference — a future reader (or a fresh build
session) should be able to start from here without re-discovering any of it.

---

## 1. OAuth 2.0 in general (the concepts)

OAuth 2.0 is a standard for letting an application access an API **on behalf of a
user/account without the app ever handling that account's password**. Instead of
a password, the app ends up holding a short-lived **access token** that it sends
with each API request.

### The actors

- **Resource owner** — the account that owns the data (here: the
  `democratsabroad` NationBuilder nation).
- **Client** — the application requesting access (here: our script / Lambda; the
  app we registered under "Your apps").
- **Authorization server** — issues tokens after verifying identity and consent
  (here: `https://democratsabroad.nationbuilder.com/oauth/...`).
- **Resource server** — the actual API serving data (here:
  `https://democratsabroad.nationbuilder.com/api/v2/...`).

### The credentials

- **Client ID** — public identifier for the app. Low-sensitivity; it even
  appears in the authorize URL.
- **Client Secret** — the app's password. **Sensitive** — must stay out of
  source control (in `.env` locally, Secrets Manager in AWS).
- **Redirect URI (OAuth callback)** — a URL registered with the app. After the
  user authorizes, the authorization server sends the browser back here with a
  one-time code. It must match the registered value **exactly** (scheme, host,
  port, path), or the flow is rejected.

### The tokens

- **Authorization code** — a short-lived (~10 min), **single-use** value handed
  back via the redirect after the user clicks "Authorize". It is not a token; it
  is exchanged for tokens. Using it twice fails.
- **Access token** — the bearer credential sent on each API call
  (`Authorization: Bearer <token>`). Short-lived — **24 hours in NationBuilder
  V2**.
- **Refresh token** — a durable credential used to obtain a *new* access token
  when the old one expires, **without any human/browser step**. This is what
  makes unattended/scheduled jobs possible.

### The "Authorization Code" flow (what we use)

This is the standard flow when a human can authorize once in a browser:

```
1. App builds an authorize URL (client_id + redirect_uri + response_type=code)
   and the user opens it in a browser.
2. User logs in (already logged in here) and clicks "Authorize".
3. Auth server redirects the browser to the redirect URI with ?code=XXXX
   (single-use, short-lived).
4. App exchanges that code (+ client_id + client_secret + redirect_uri) at the
   token endpoint for an access_token AND a refresh_token.
5. App calls the API with Authorization: Bearer <access_token>.
6. When the access token expires (24h), the app POSTs the refresh_token to the
   token endpoint to get a fresh access_token (+ usually a new refresh_token),
   with no browser step. Repeat forever.
```

The one-time human step is #1-#3. Everything after is automatable.

### PKCE (optional, not used here)

PKCE ("pixie") adds a one-time `code_verifier`/`code_challenge` pair so an
intercepted authorization code can't be exchanged by an attacker. NationBuilder
supports it optionally. We did **not** use it — for a server-side client that
keeps its client secret private, the plain authorization-code flow is sufficient.
If we later want it, add `code_challenge`/`code_challenge_method=S256` to the
authorize request and `code_verifier` to the token exchange.

---

## 2. NationBuilder specifics

- **Access requires** a NationBuilder **Certified Developer** account or a nation
  on an **Enterprise/Network plan**. Developer tools live under
  **Settings → Developer**.
- **V1 vs V2:** V1 uses a static access token passed as an `access_token` query
  param and is being phased out. **V2 uses OAuth 2.0 bearer tokens**, a different
  base path (`/api/v2`), and a JSON:API-style response shape. V2 access tokens
  expire after 24h and need the refresh flow.
- **Where things are in the control panel:**
  - `Settings → Developer → Register App` — create an OAuth app. Set the OAuth
    callback (redirect URI); set status **private**; leave all **syndication**
    options off (those publish/share the app to other nations — not wanted).
  - `Settings → Developer → Your apps` — lists apps; open one to see its
    **Client ID**, **Client Secret**, and callback URL.
  - `Settings → Developer → API Testing` — V1-oriented interactive docs / test
    token. Not used for V2 OAuth.
- **OAuth endpoints** (nation-scoped):
  - Authorize: `https://democratsabroad.nationbuilder.com/oauth/authorize`
  - Token: `https://democratsabroad.nationbuilder.com/oauth/token`
- **API base:** `https://democratsabroad.nationbuilder.com/api/v2`

---

## 3. What was set up for this project (done)

1. **Registered an OAuth app** in `democratsabroad` under "Register App":
   - Status: private; syndication: all off.
   - OAuth callback URL: `https://localhost:8080/callback`
2. **Credentials** stored in `.env` (gitignored):
   - `NATIONBUILDER_CLIENT_ID`
   - `NATIONBUILDER_CLIENT_SECRET`
   - `NATIONBUILDER_REDIRECT_URI=https://localhost:8080/callback`
3. **One-time authorization handshake** completed:
   - Opened the authorize URL in a browser, clicked Authorize, copied the
     `?code=...` from the (non-loading, expected) `https://localhost:8080/...`
     redirect.
   - Exchanged the code at the token endpoint for an access + refresh token.
4. **Tokens persisted** to `.env`:
   - `NATIONBUILDER_ACCESS_TOKEN_V2` — 24-hour access token.
   - `NATIONBUILDER_REFRESH_TOKEN` — durable; used to mint new access tokens.

> The `localhost` redirect never needs a running server. The browser failing to
> load `https://localhost:8080/callback` (and showing a cert warning first) is
> expected — the only thing we need is the `code` in the address bar.

### Re-running the handshake (if ever needed)

The authorize URL (fill in / confirm the client id):

```
https://democratsabroad.nationbuilder.com/oauth/authorize?client_id=<CLIENT_ID>&redirect_uri=https%3A%2F%2Flocalhost%3A8080%2Fcallback&response_type=code
```

Because the code is single-use, a fresh code is required each time you redo the
exchange. The token exchange is a POST to `/oauth/token` with
`grant_type=authorization_code`, the `code`, `client_id`, `client_secret`, and
`redirect_uri`.

---

## 4. Token refresh flow (needed for the scheduled job)

V2 access tokens die after 24h, so the nightly job must refresh. POST to the
token endpoint with the refresh token:

```
POST https://democratsabroad.nationbuilder.com/oauth/token
{
  "grant_type":    "refresh_token",
  "refresh_token": "<current refresh token>",
  "client_id":     "<client id>",
  "client_secret": "<client secret>"
}
```

The response contains a **new** `access_token` and (typically) a **new**
`refresh_token`, both with `expires_in` = 86400. Implementation notes for the
build:

- **Rotate storage:** persist the newly returned refresh token, since providers
  commonly rotate it on each refresh. In AWS, store it in Secrets Manager and
  write the rotated value back after each refresh.
- **Just-in-time refresh:** simplest robust pattern for the Lambda is to ignore
  the stored access token and **always refresh at the start of a run** (one extra
  call, always-valid token). Alternatively, track `created_at + expires_in` and
  only refresh when near expiry.
- **Store in Secrets Manager**, same pattern as the existing secret
  (`nb/insights/prod` or a new secret), loaded via the existing
  `_load_secret_into_env()` mechanism. Keys would be `NATIONBUILDER_CLIENT_ID`,
  `NATIONBUILDER_CLIENT_SECRET`, `NATIONBUILDER_REFRESH_TOKEN`.

---

## 5. Confirmed V2 request/response shapes

All verified against live `democratsabroad` data.

### Auth header

```
Authorization: Bearer <access_token>
Accept: application/json
```

### Pull signups with the registered address

```
GET /api/v2/signups?extra_fields[signups]=registered_address&page[size]=100
```

- `extra_fields[signups]=registered_address` sideloads the registered address
  into each signup's `attributes` (per `Dennis.md`). Confirmed working.
- `/api/v2/signups/{id}?extra_fields[signups]=registered_address` fetches one.

### Response shape (JSON:API)

```json
{
  "data": [
    {
      "type": "signups",
      "id": "676",
      "attributes": {
        "federal_district": "NY13",
        "registered_address": {
          "address1": "535 W 110th St",
          "city": "New York",
          "zip": "10025-2086",
          "state": "NY",
          "country_code": "US",
          ...
        },
        "state_lower_district": "...",
        "state_upper_district": "...",
        ...
      }
    }
  ],
  "links": { "self": "...", "next": "..." },
  "meta": {}
}
```

### The exact fields the two checks need

| Needed value | Path in V2 response |
|--------------|---------------------|
| signup id | `data[].id` |
| registered state | `data[].attributes.registered_address.state` |
| registered zip | `data[].attributes.registered_address.zip` (may be ZIP+4; take first 5 digits) |
| federal district | `data[].attributes.federal_district` (e.g. `"NY13"`; prefix `NY` is the state) |

Notes:
- `registered_address` can be `null` (e.g. system/admin record id=1, and members
  with no registered US address). Handle null.
- `zip` may be ZIP+4 (`10025-2086`) or 5-digit (`48433`) — normalize to the first
  five digits for ZIP/state validation.
- `federal_district` is the authoritative value (this is *why* we moved off
  Insights). Its two-letter prefix is the state for the federal-district check.

### Pagination

- Page via `page[size]=N` (100 is a sensible size; 50 confirmed working).
- Follow `links.next` until it is absent. The nation has ~188k signups, so a full
  pull is many pages — budget for it (this is why the Lambda timeout is 900s and
  why we may filter to US-registered signups).

### Filtering (optional, for efficiency)

V2 supports `filter[...]`. Per the core-concepts docs, nested address filters use
the resource type, e.g.
`/api/v2/signups?include=registered_address&filter[addresses][state]=MT`.
(A naive `filter[registered_address_state]=...` returns HTTP 400 — use the
documented nested form.) Filtering to signups that have a US registered address
could cut the pull dramatically versus all ~188k; worth exploring in the build.

---

## 6. Build plan (next steps — not yet done)

The actual implementation is intentionally left for the focused build session:

1. **Add a V2 client** (new module): Bearer auth, `/api/v2` base, JSON:API
   parsing, pagination via `links.next`, and a `refresh_access_token()` using the
   refresh-token flow in section 4.
2. **Pull all signups** with `extra_fields[signups]=registered_address`, project
   to `signup_id`, `registered_state`, `registered_zip` (normalized),
   `federal_district`.
3. **Run the two original checks** on that data:
   - ZIP vs. state (reuse the `ZIP_Locale_Detail.xlsx` three-sheet lookup).
   - Federal district prefix vs. state.
4. **Drop all Insights/Tableau code**: remove `tableau-api-lib`, `nb_insights.py`,
   the Insights view pull, and the V1 cross-check (NB is now the only source, so
   there is no "Insights vs NB" comparison — just validate NB's own fields).
5. **Keep** the output/report structure (CSV+XLSX, dated S3 objects, SES email
   attachments) and the Lambda/packaging/scheduling scaffolding; just repoint the
   data source.
6. **Secrets:** move `NATIONBUILDER_CLIENT_ID/SECRET/REFRESH_TOKEN` into Secrets
   Manager for the deployed Lambda; `.env` holds them locally.

---

## 7. Security notes

- `.env` holds the client secret and tokens; it is gitignored — never commit it.
- `.env.example` must contain only placeholders for the `NATIONBUILDER_*` keys.
- The client secret and refresh token are the sensitive values; the client ID and
  access token are less so (the access token self-expires in 24h).
- The client secret can be regenerated from "Your apps" if it is ever exposed;
  doing so invalidates the old secret and requires re-authorizing.
