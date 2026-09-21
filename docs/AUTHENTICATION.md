# Authentication

Before Phase 12 the application had **no authentication**: every request acted as a seeded development owner. Phase 12 built it
(it did not harden an existing system, because there was none). This page says what exists, how it is protected, and what is not built.

## The pieces

| Piece | Where | What it is |
| --- | --- | --- |
| `accounts` | table | one sign-in identity per email: name, Argon2id password hash, status, failed-attempt count, pause time |
| `users` | table | the person's **membership** of one shop (see `STAFF_MANAGEMENT.md`); a person in two shops has one account and two of these |
| `auth_sessions` | table | one row per signed-in browser or client; only **hashes** of the session token and CSRF token are stored |
| `password_service` | service | Argon2id hashing, password rules |
| `auth_service` | service | sign in, resolve a session on every request, choose a shop, sign out, change password |
| `api/deps.py` | API | reads the cookie (or Bearer token), builds the request context, applies the central permission check |

## Sign in

`POST /api/v1/auth/login {email, password}`.

* The email is lower-cased and trimmed. A wrong email and a wrong password get the **same** answer (401 "Invalid email or
  password.") and take about the same time (a throw-away Argon2 verification is spent for an unknown email).
* **Throttling**, three layers: a rate limit per client address, a rate limit per email (`KIRANA_RATE_LIMIT_AUTH` per window),
  and a **pause of the account** after `KIRANA_LOGIN_MAX_FAILURES` (5) wrong passwords in a row, for `KIRANA_LOGIN_LOCKOUT_MINUTES`
  (15). While paused even the right password gets HTTP 429 with `Retry-After`. A success clears the count. Failures are recorded as
  platform security events (account id only: never the email typed, never the password).
* On success a session is created. If the person has exactly one active shop it is selected; if they have several the app asks
  which one (`POST /auth/select-shop`); with none, business calls answer 403 "You do not have access to any shop right now."
* Accounts can be disabled (`account_cli disable`); a disabled account cannot sign in and its sessions end.

## The session

* A session is an opaque random token (`secrets.token_urlsafe(32)`). The browser gets it in an **HttpOnly, SameSite=Lax cookie**
  (`Secure` in production, see below); JavaScript cannot read it. It is never in a response body, except when a non-browser client
  asks for `?issue_token=true`, and then it is sent as `Authorization: Bearer` (no cookie, so no CSRF concern).
* The database stores only its SHA-256 hash, so a database leak does not hand out sessions.
* **Expiry**: an idle limit (`KIRANA_SESSION_IDLE_MINUTES`, default 60) and an absolute limit (`KIRANA_SESSION_ABSOLUTE_HOURS`,
  default 12). Any call renews the idle timer (written at most once a minute). There is no separate refresh token: the sliding idle
  window plus the absolute limit is the refresh mechanism, and a person who wants to keep working after 12 hours signs in again.
  The frontend warns 5 minutes before the idle limit ("Stay signed in") and, if the session ends anyway, asks for the password in
  a box **over the page**, so unsaved forms are not lost.
* **Every request re-checks everything**: the session is unrevoked and unexpired, the account is active, the membership is ACTIVE,
  and the role's permissions are read fresh. A suspension, removal or role change therefore takes effect on the next call. Nothing
  is cached.
* **Sign out** (`POST /auth/logout`) revokes the session at once (a copied cookie stops working) and clears the cookies.
* **Changing a password** needs the current one and a good new one, and **ends every other session** of that account.

## CSRF

A browser attaches cookies to any request to the site, so state-changing calls made with the cookie need a second proof. The server
issues a per-session CSRF token (also stored only as a hash), which the app reads from a non-HttpOnly cookie and echoes in
`X-CSRF-Token` on every POST/PUT/PATCH/DELETE. A missing or wrong token is 403. A token from another session does not work. Reads
need no token. Bearer-authenticated calls need none. SameSite=Lax is a second layer, not a substitute.

## Passwords

* **Argon2id** (`argon2-cffi`), with the parameters inside the hash. Production requires at least 19 MiB and 2 passes; the default is
  64 MiB and 3 passes (`KIRANA_PASSWORD_HASH_*`). A hash below the current cost is **upgraded at the next successful sign-in**.
* Rules: at least `KIRANA_PASSWORD_MIN_LENGTH` (10) characters, at most 128, not a very common password, not one or two repeated
  characters, not containing the email's name. Length matters more than symbols.
* A password is never stored, logged, returned, put in a URL, or kept in the browser after the request (the forms clear it).
* Nothing logs a password, session token, CSRF token or invitation token: the logging layer drops fields with those names and tests
  scan the captured logs for the actual values.

## Bootstrapping and operators

There is **no public sign-up**. Operators use the command line:

```bash
python -m app.account_cli create-shop --shop-name "Sharma Store" --owner-email owner@example.com --owner-name "R Sharma"   # prints a one-time password
python -m app.account_cli set-password --email owner@example.com   # a new one-time password; ends all their sessions
python -m app.account_cli unlock|disable|enable --email ...
python -m app.seed    # development only: a dev shop and owner; the password is printed once (or KIRANA_DEV_OWNER_PASSWORD)
```

Existing databases: migration `0015` gives every existing user an account (same email and password hash). The development owner's
hash was "!" (no password), so run `python -m app.seed` once to set one.

## Development shortcut

`KIRANA_DEV_AUTH_BYPASS=true` makes every request act as the seeded owner with no sign-in. It is **off by default**, ignored in
production, and production **refuses to start** if it is set.

## Password reset

**Not built.** A self-service reset needs a way to reach the person (email or SMS) and no provider is bundled or faked. The safe
substitute today: an operator runs `account_cli set-password`, which ends the person's sessions and shows a one-time password to
hand over. The architecture for a reset is the invitation mechanism (a random, expiring, single-use, hash-stored token); it needs only a
delivery channel.

## Security assumptions

* HTTPS in front of the application in production (the cookie is `Secure`). Locally, plain HTTP is normal and the cookie is not `Secure`.
* One origin for the app and the API (a reverse proxy, or the Vite proxy in development). Cross-origin use needs
  `KIRANA_CORS_ORIGINS` to list the exact origin and `VITE_API_BASE_URL`; credentials are then sent to listed origins only.
* Rate limiting is per process (in memory). With several web workers each keeps its own window; the account pause is in the
  database and is shared.
* Behind a proxy set `KIRANA_TRUST_PROXY_HEADERS=true` **only** when the proxy sets `X-Forwarded-For`, otherwise a client could fake it.
* Not built: multi-factor authentication, single sign-on, "sign out everywhere" button, listing active devices, email-based reset.
