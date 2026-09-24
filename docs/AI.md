# AI assistant

The assistant is **read-only and optional**. With no provider configured (`KIRANA_AI_PROVIDER` and `AI_API_KEY` unset) it answers its ready-made questions and
says "AI Assistant is not configured." for free-form wording. Nothing is faked.

* **No arbitrary SQL.** The model can only call a fixed set of typed tools (sales, inventory, customers, finance, analytics, CRM) that run the same services and
  reporting layer as the screens, scoped to the caller's shop and permissions. Tool arguments are validated; results contain numbers computed by the application.
* **The AI never writes.** The only path from a model to the database is a person-confirmed *AI action* (preview → Confirm/Edit/Cancel), and only three exist: a purchase **draft**, a stock adjustment (with a reason code) and an offer **draft**. Posting, refunds, price changes and khata entries are not actions the AI can prepare. Every step is audited without storing the prompt.
* **The key is backend-only:** never in the frontend, the database, logs or Git. Usage is metered per plan.
* Tool references: [ANALYTICS_AI_TOOLS.md](ANALYTICS_AI_TOOLS.md), [FINANCE_AI_TOOLS.md](FINANCE_AI_TOOLS.md), [CRM_AI_TOOLS.md](CRM_AI_TOOLS.md). Architecture guards: `tests/test_ai_architecture.py`.
* **Not verified:** behaviour with a live model provider. All AI tests use a fake provider.
