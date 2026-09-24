# Campaigns and reactivation (Phase 14)

## Lifecycle

`DRAFT → SCHEDULED → RUNNING → COMPLETED`, with `PAUSED` and `CANCELLED` (cancel needs a reason; nothing is deleted).
Only DRAFT/SCHEDULED campaigns can be edited. A campaign targets **either** a saved group **or** a segment (not both),
carries an optional existing `Promotion` (no second discount system) and a message. Channels: IN_APP, EMAIL, SMS, WHATSAPP,
PUSH.

## What "launch" really does

`POST /campaigns/{id}/launch` (needs `CAMPAIGN_LAUNCH`) snapshots the audience (`campaign_audience_snapshots`, insert-only) and
records **one honest outcome per customer** (`campaign_sends`, insert-only):

| Status | Meaning |
|---|---|
| `SKIPPED_NO_CONSENT` | EMAIL/SMS/WHATSAPP/PUSH and the customer has not opted in to that channel (checked first). |
| `NOT_CONFIGURED` | Consent is fine but **no provider exists** for that channel. This is what every send is today. |
| `SENT` | Reserved for a real provider; unreachable today. |
| `SKIPPED_OPTED_OUT`, `FAILED` | Reserved for provider integrations. |

No message is ever delivered by this codebase and nothing pretends otherwise. The campaign is then marked COMPLETED (there is
no async queue). Deactivated customers are never in an audience.

Launching above the shop's `campaign_audience_threshold` waits for a second person's approval (see [CRM.md](CRM.md)).

## Reactivation workflow

1. `GET /crm/reactivation/preview?inactive_days=60&cooldown_days=30&channel=SMS` — who would be contacted and, per customer, a
   stated verdict: `ELIGIBLE` ("No purchase for 74 days; typical interval 28.0 days."), `NO_CONSENT`, or `IN_COOLDOWN`
   (contacted by a campaign inside the cooldown). Sends nothing.
2. `POST /crm/reactivation/draft` (needs `CAMPAIGN_MANAGE`) — snapshots the eligible customers into a manual group and creates a
   **DRAFT** campaign. Nothing is sent.
3. Launch is the normal, separately permissioned (and possibly approval-gated) step.

Opt-outs are honoured (consent is re-read every time), a cooldown of 0 turns the frequency limit off, and nobody is discounted
automatically: any promotion must be attached by a person.

## Endpoints

`GET|POST /campaigns`, `GET|PATCH /campaigns/{id}`, `GET /campaigns/{id}/audience`, `POST /campaigns/{id}/schedule|launch|pause|resume|cancel`,
`GET /campaigns/{id}/sends`, `GET /exports/campaigns/{id}`.

## Real integrations required

Email (SMTP/API), SMS, WhatsApp Business and push each need a provider registered in `notification_service.provider_for`. Until
then every send is `NOT_CONFIGURED`. Scheduling stores `start_at` only; nothing launches a SCHEDULED campaign automatically.
