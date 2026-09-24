# CRM AI tools (Phase 14)

All new tools are **read-only** and go through the existing assistant pipeline (`ai_tools.TOOLS`/`TOOL_PERMISSION`, kept in
key-sync by a test). Every number is whatever the underlying service returns — a missing program says `NOT_CONFIGURED`, an
unknown customer says `NO_DATA`; nothing is invented.

| Tool | Permission |
|---|---|
| `get_customer_profile` | `CRM_VIEW` (includes purchase totals, outstanding, loyalty balance, segments) |
| `get_customer_segments` | `CRM_VIEW` |
| `get_inactive_customers` | `CRM_VIEW` |
| `get_customer_retention_summary` | `CRM_ANALYTICS_VIEW` |
| `get_reactivation_candidates` | `CRM_ANALYTICS_VIEW` |
| `get_customer_growth_dashboard` | `CRM_ANALYTICS_VIEW` |
| `get_loyalty_summary` | `LOYALTY_VIEW` |
| `get_campaign_summary` | `CAMPAIGN_VIEW` |
| `get_referral_summary` | `REFERRAL_VIEW` |

Purchase-frequency and lifetime-value figures are folded into `get_customer_profile` rather than being separate tools.

## The one new action

`CAMPAIGN_DRAFT` follows propose → confirm → authorize → execute → audit: proposing creates nothing; confirming creates a
**DRAFT** campaign through `campaign_service.create` (permission `CAMPAIGN_MANAGE`). The payload has no status field
(extra fields are rejected), so it cannot launch. The AI can never send a campaign or message, refund, award or adjust
points, change a balance or a price; launching remains a person's `CAMPAIGN_LAUNCH` action.
