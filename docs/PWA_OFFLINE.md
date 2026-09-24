# Mobile, PWA and offline operations (Phase 18)

One application, one backend. There is no mobile backend and no second data model: the browser app is installable, loads offline, and can queue a
few kinds of work that the SAME backend services apply when the connection returns.

## Installable app (PWA)

* `manifest.webmanifest` (standalone, icons 192/512 and a maskable icon, two shortcuts) and a hand-written service worker `public/sw.js`. No
  library and no build dependency were added; a small Vite plugin writes `precache-manifest.json` (every script and stylesheet of the release) and stamps
  the worker with a build id, so each release installs as a new worker and the previous cache is removed.
* The service worker caches **the app shell only**. It never handles `/api/`, `/health`, `/metrics` or `/docs`, only same-origin GETs, only 200
  answers, and contains no credential. Business data is therefore never in a browser cache managed by the worker.
* Updates: a new worker waits; the app shows "A new version is ready" and switches only when the person taps *Update now*, so a release never swaps
  under a sale in progress. The app checks for a release when it returns to the foreground.
* Install: the button appears only when the browser itself offers installation (`beforeinstallprompt`); nothing is faked.
* Verified in a real browser against the production build: the worker registers and activates, 134 release files are cached, no `/api` response is
  cached, and with the server stopped the app shell (sign-in screen) still loads.

**Supported browsers.** Installation and offline shell: current Chrome and Edge (Android, desktop), Safari on iOS/macOS (add to Home Screen; no install
prompt), Firefox desktop (offline shell; installation depends on platform). Camera barcode scanning needs the `BarcodeDetector` API (Chrome/Edge, some
Safari); elsewhere a USB/Bluetooth scanner (types the code and Enter) or manual entry works. IndexedDB and service workers need a secure origin (HTTPS, or localhost).

## What works offline

| Capability | Offline? | Notes |
| --- | --- | --- |
| Open the app and any screen's code | yes | shell + all scripts precached; screens that need data show their normal error |
| Read products (price, stock) and customers (name, phone) | yes, **if the person turned the device copy on** | a snapshot labelled "Last synced: ..."; warns when older than 3 days; no balances, cost or profit are copied |
| Record a quick sale, a sale of products, a customer payment | yes | queued on the device, sent when online |
| Inventory adjustments, purchases, returns, finance, reports, AI | no | adjustments depend on the current count, so an offline one is not safe to replay |
| Recent sales, khata balances | no | not copied: they would be stale and are sensitive |

The offline till page (`/offline`) is reachable from the offline banner. It is the only new screen; the normal screens are unchanged.

## Queue and sync

Each operation gets a client-generated id once (`op-...`) and is stored with status `PENDING -> SYNCING -> SYNCED`, or `FAILED` / `CONFLICT` (kept, with
the server's reason) or `DISCARDED` (a person dropped it). It is sent oldest first to `POST /api/v1/sync/operations` (up to 25 per request) which runs
each through the existing services: `quick_sale_service`, `sale_service` (create draft, price check, post) and `khata_service.record_payment`. There is
no second inventory, sales or khata engine.

* **Exactly once.** `sync_operations` has a unique `(shop, client_op_id)`. For an applied operation the row is written **in the same transaction** as
  the sale or payment, so both exist or neither. Sending it again (lost answer, crash, second tab, second device) returns the stored answer and does
  nothing. An id replayed with a different body still returns the original answer. Tested with simultaneous requests.
* **Network failure** leaves the operation `PENDING` with the same id; **401** pauses the sync and keeps everything; an unexpected server error answers
  `RETRY` and stores nothing.
* One sync runs at a time (in-tab guard and Web Locks across tabs). Auto-sync runs on reconnect and every 30 s while online with items waiting.
* Payload shape: exactly the fields the normal API takes, plus `expected_total` (what the customer was quoted).

## Conflicts (the backend is always authoritative)

A conflict is never adjusted to fit: the server changes no price, quantity or date.

* **Not enough stock** (e.g. two devices sold the last units): `CONFLICT insufficient_stock`; nothing was recorded, not even a draft.
* **Price changed while offline:** the sale sends no unit price, so the server prices it at today's price; if that differs from `expected_total` the
  result is `CONFLICT total_changed` with both totals shown.
* **Closed financial period, missing customer, no permission:** `CONFLICT`/`FAILED` with the reason.
* Resolution: **Try again** re-runs the exact stored payload (stock may have arrived); **Drop it** tells the server (so it can never be applied later)
  and marks it dropped. Only conflicts and failures can be dropped; a synced operation has already happened.

## Device security

* IndexedDB, one database per **shop and person** (`shop-offline:<shop>:<user>`); another shop or person on the same device gets a different database and
  the app never opens it. IndexedDB is **not encrypted by the app**: anyone with the unlocked device and browser profile can read it. So the data is small,
  the copy is opt-in, and the offline page says so.
* No password, session token or CSRF token is stored. Sessions stay in the HttpOnly cookie. When the session expires the queue is kept and resumes
  after sign-in.
* Sign-out: waiting items are sent if online; the product/customer copies are always deleted; unsynced work is **never destroyed without the person
  choosing** (keep for next sign-in, or delete). *Remove offline data from this device* deletes copy and queue.
* The server permissions still apply per operation type (quick sale: `QUICK_SALE_CREATE` + `SALE_POST`; sale: `SALE_CREATE` + `SALE_POST`; payment:
  `KHATA_PAYMENT`); snapshots need `PRODUCT_VIEW`+`INVENTORY_VIEW` / `CUSTOMER_VIEW`. Sync is audited with the device id and client time.

## Scanner

`CameraScanButton` reads a barcode with the camera (only while its panel is open; nothing is recorded or uploaded) and hands the code to the **same**
lookup as a typed or USB-scanned one (`/products/lookup` online). Offline the code is matched exactly against the device copy (barcode or SKU); the
server's EAN/UPC variant matching applies whenever online.

## Mobile performance (measured)

Route-level lazy loading and an on-demand Hindi bundle: the first download fell from **1,121 kB (279 kB gzip) to 418 kB (130 kB gzip)**; each screen loads
as a 5-30 kB chunk when first opened (and is precached for offline). All tables scroll horizontally, the sidebar is a drawer on small screens, and the
viewport meta uses `viewport-fit=cover`.

## Limitations

No device-level visual audit was run (no physical phone here); layouts were checked by structure. Offline detailed sales cannot apply promotions or
coupons (the server applies what it finds; a different total is a conflict). Only exact code matching works offline. Old synced entries are kept 24 h.
Background Sync (sending while the app is closed) is not used: sending happens when the app is open. iOS may clear IndexedDB for sites unused for weeks.
