# Integrations and ecosystem (Phase 17)

The application talks to outside services through one small framework, so no business rule depends on a vendor:

```
business code -> a service (payments, messaging, exports) -> integration_service -> an adapter (app/integrations) -> the outside world
```

**Honesty first.** An integration is reported as configured only when its provider is chosen *and* its credentials really exist in the
environment. Otherwise the answer is **Provider Not Configured** (or *Credentials Not Configured*). Nothing is ever recorded as sent, paid
or refunded unless a provider (or an authorised person, for a provider with no outside system) said so.

## What exists, and how far it has been proven

| Integration | Provider(s) | Proven how | Not proven |
| --- | --- | --- | --- |
| Email | `smtp` (any SMTP server) | Real SMTP conversation with a local test server: send, reject, 4xx retry, bad login, header injection | STARTTLS/SSL against a TLS server; any real mail service |
| SMS, WhatsApp, Push | `http_json` (HTTPS POST of `{channel,to,title,message}` with a bearer token) | Real HTTP requests to a local test gateway (auth, 4xx, 5xx, redirects refused, SSRF checks) | Any vendor: none is named or claimed; a relay adapts other APIs |
| Payments | `manual` (cash on delivery / UPI confirmed by a person), `generic_webhook` (signed webhooks from any gateway or relay) | Full lifecycle, signatures, duplicates, mismatches, refunds, khata application, using the real code paths | **No payment gateway is integrated.** There is no card or UPI processing here, and no real gateway has been tested |
| Storage | `local` (default), `s3` (any S3-compatible service, hand-written Signature V4) | AWS's published key-derivation vector and a full put/get/delete against a local server that re-computes the signature | A live S3-compatible service |
| Accounting | generic CSV/Excel export with your account-code mapping | Built from the finance ledger; row totals equal the ledger | Import into any accounting product: no product compatibility is claimed |
| Maps / location | distance between two known points (no provider); geocoding needs a provider (none bundled) | Distance arithmetic | Geocoding: **Provider Not Configured** |
| Barcode / product data | the existing UPCitemdb / Open Food Facts price providers | Existing tests; they only *suggest* | Live calls in this environment |

There is **no online ordering module** in this application, so payments link to a sale, a quick sale and/or a customer (plus a free
`order_ref` text), never to an order. Password-reset and order-update emails do not exist for the same reason.

## Configuring an integration

`PUT /integrations/{type}` with `provider`, non-secret `config`, and the **name** of an environment variable holding the credential
(`credential_ref`; `webhook_credential_ref` for webhooks). Types per shop: `PAYMENT`, `EMAIL`, `SMS`, `WHATSAPP`, `PUSH`, `ACCOUNTING`.

* A reference must look like `KIRANA_INTEGRATION_NAME`. The application reads **only** variables with that prefix, so a configured name can
  never be used to read the database URL, the signing key or anything else. A setting that looks like a secret (`password`, `token`, `key`...)
  is refused with advice to use an environment variable.
* Status is worked out, not set: `NOT_CONFIGURED` (no provider, missing setting, or the variable is empty), `DISABLED`, `CONFIGURED`,
  `ERROR` (three consecutive failed calls; one success clears it).
* URLs must be `https` (plain `http` to localhost only outside production), carry no user name or password, and are checked against
  server-side request forgery before *every* call: hosts that resolve to private, loopback, link-local (cloud metadata) or reserved addresses
  are refused. Redirects are not followed.
* `POST .../test` checks the connection and credentials **without sending a message or moving money**. `rotate-credentials` points at a new
  variable (the operator sets its value; nothing is copied) and records the rotation. `rotate-webhook-key` issues a new public webhook address and
  the old one stops working at once.
* Every change is audited (type, provider, whether a reference is set: never a value).

## Secrets

Never in the database, a response, a log line, an audit record, an error message or the frontend (tests search for them in all of those).
Development: put values in `.env` (ignored by git). Production: use your platform's secret manager to set the environment. Rotation: set a
new variable, call `rotate-credentials`, remove the old one. `.env.example` lists the variable names; it contains no values.

## Payments

Provider-neutral states: `CREATED -> PENDING -> AUTHORIZED -> CAPTURED -> PARTIALLY_REFUNDED -> REFUNDED`, with `FAILED` and `CANCELLED` final.
Methods: `UPI`, `CARD`, `PAYMENT_LINK`, `ONLINE_PAYMENT`, `COD`. Purposes: `SALE_PAYMENT` and `KHATA_PAYMENT`.

* **Not a ledger.** A `SALE_PAYMENT` is evidence that a sale already on the books was paid this way and posts nothing. A `KHATA_PAYMENT` is
  recorded **once**, through `khata_service.record_payment`, when it is captured; the entry id is stored on the payment, so a repeated webhook, a
  re-check or a retry cannot record it twice. Finance reads that entry where it already lives. If the khata entry cannot be written the payment
  stays CAPTURED (the money did arrive) and is flagged for review.
* **The browser never says a payment succeeded.** There is no route that accepts a status. A status changes only from (a) a signature-verified
  webhook, (b) a status check the server makes to the provider (`POST /payments/{id}/verify`), or (c) a person's recorded attestation for a
  provider with no outside system (`manual` only; an external provider's payment cannot be confirmed or cancelled by hand).
* **Checks before believing an event:** the amount must equal the payment's amount, an out-of-order event is ignored, a capture for a payment
  recorded as failed or cancelled is *not applied* and flags the payment for review. A refund reported by the provider is tracked cumulatively; if
  the payment was applied to the khata, it is flagged: reverse that ledger entry (the existing khata reversal) first. A refund made here for a
  khata payment is refused until that entry is reversed.
* **Create** (`POST /payments`) and **refund** need an `Idempotency-Key`. The same key returns the same payment and calls the provider once;
  the same key with a different request is refused. If a provider call's outcome is unknown, the payment is flagged for review and settled with a
  status check, never by repeating the create.
* Permissions: `PAYMENT_INTEGRATION_MANAGE` to create, verify, confirm, cancel, refund; `INTEGRATION_VIEW` to read.

## Webhooks

`POST /api/v1/webhooks/{webhook_key}` is the only intentionally public integration endpoint. The key is unguessable and identifies the
integration; **nothing is believed until the signature is checked** with the shop's secret.

Signed format (`generic_webhook`): headers `X-Kirana-Timestamp` (unix seconds, within 5 minutes) and `X-Kirana-Signature` = hex
HMAC-SHA256 of `"<timestamp>." + <raw body>`. Body: `event_id`, `type` (`payment.status`), `txn_id`, `status`, `amount`, optional `refunded`,
`failure_code`; amounts are decimal text with at most two places (floats are refused).

* Bad signature, stale timestamp, unknown or disabled key, missing secret: a bare refusal (401 or 404) that reveals nothing, a security event is
  noted, and **no row is stored** (an attacker cannot fill the table). A body over 64 KB is refused; the endpoint is rate limited
  (`KIRANA_RATE_LIMIT_WEBHOOK`).
* `webhook_events` is unique per (integration, event id): a redelivery is acknowledged and counted, never re-applied; a redelivery of an event
  that FAILED is retried (applying a status is itself idempotent); an event id reused with a different body is refused. Statuses: `RECEIVED`,
  `PROCESSING`, `PROCESSED`, `FAILED`, `IGNORED`. Answers are 200 for everything the sender need not retry.
* Events for a transaction that belongs to another shop are ignored: a payment is looked up only inside the integration's own shop.

## Messages (email, SMS, WhatsApp, push)

`POST /integrations/messages` (`NOTIFICATION_INTEGRATION_MANAGE`, optional `Idempotency-Key`) records one `message_deliveries` row per attempt
with the honest outcome: `SENT`, `QUEUED` (temporary failure: retried by the `integrations.retry_messages` background job with backoff, up to
`KIRANA_NOTIFICATION_MAX_ATTEMPTS`), `FAILED`, `NOT_CONFIGURED` (**Provider Not Configured**), `SKIPPED_NO_CONSENT`, `SKIPPED_NO_CONTACT`.

* **Marketing needs the customer's opt-in for that channel** (the CRM consent flags, default *not opted in*); consent withdrawn before a retry
  stops the message. **Transactional** messages (order, payment, delivery, invoice) do not need marketing consent but need a contact detail
  and a configured provider. A campaign uses the same path (one attempt per customer; a temporary failure is recorded `FAILED`, never `SENT`).
* Staff notifications (`notification_service`) use the shop's provider first, then the platform default. Scheduled reports can be emailed: the
  body is the summary figures (counts and totals, no names); a shop with no email integration gets *Delivery Channel Not Configured*.
* **Retry rule.** Only a failure that could not have delivered anything (could not connect, HTTP 429/5xx, SMTP 4xx) is queued for retry. An
  unknown outcome (a timeout after sending) is `FAILED` and never repeated blindly. Reads and status checks (`SAFE`) are retried up to three
  times on transient errors; anything that sends a message or moves money (`UNSAFE`) is tried once.

## Storage

`KIRANA_STORAGE_PROVIDER=local|s3`. Keys are `<shop id>/<sha256>.<jpg|png|webp>`, validated identically for every store, so a path can never come
from user input; files are private and served only through the authenticated, shop-scoped endpoints; the existing type, size and content
validation is unchanged. `POST /integrations/platform/storage/test` (`STORAGE_INTEGRATION_MANAGE`) writes, reads and deletes one probe object.

## Accounting exports

`GET /integrations/accounting/export/{transactions|invoices|customers|suppliers|tax}?date_from&date_to&format=csv|xlsx`. Built from the
finance ledger and existing documents; no second ledger. Map your account codes with `PUT /integrations/accounting/mappings`
(`EVENT:<finance event>` and `METHOD:<payment method>`); an unmapped row is exported with a blank code and marked `UNMAPPED`. Customer and
supplier exports contain personal contact details and need the customer or supplier permission as well as `FINANCE_EXPORT`.

## Monitoring

`GET /integrations/dashboard`: per integration the provider, enabled flag, configuration status and message, last success, last failure,
consecutive failure count and last safe error code; plus platform entries (storage, product data, maps). `GET /integrations/logs`
(`INTEGRATION_LOGS`) is a payload-free call log: operation, outcome, duration, error code.

## Permissions

| Permission | Owner | Manager | Accountant |
| --- | --- | --- | --- |
| `INTEGRATION_VIEW` | yes | yes | yes |
| `INTEGRATION_TEST`, `INTEGRATION_LOGS` | yes | yes | no |
| `PAYMENT_INTEGRATION_MANAGE`, `NOTIFICATION_INTEGRATION_MANAGE` | yes | yes | no |
| `INTEGRATION_MANAGE`, `INTEGRATION_CONFIGURE`, `STORAGE_INTEGRATION_MANAGE`, `WEBHOOK_MANAGE` | yes | no | no |

Choosing a payment provider also needs `PAYMENT_INTEGRATION_MANAGE`, and a messaging provider `NOTIFICATION_INTEGRATION_MANAGE`.
Everything is scoped to the shop; another shop's configuration, payments, messages and webhook events are not found.

## Known limitations

No payment gateway, real SMS/WhatsApp vendor, live S3 service or accounting product has been exercised. SMTP STARTTLS/SSL is implemented with
the standard library but only plain SMTP was tested. Webhook processing retries a failed event only when the sender redelivers it. There is no
outbound webhook (the shop emitting events to its own systems).
