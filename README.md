# AgentBoiler

A FastAPI + Next.js starter template with PydanticAI agents, human-in-the-loop tool approvals, Stripe billing, and token usage tracking — skip the first week of agentic app boilerplate.

**Python 3.12** | **FastAPI** | **PydanticAI** | **Next.js** | **Supabase** | **Stripe**

## What's Included

- PydanticAI agent wired to FastAPI with configurable system prompt and model (default: claude-sonnet-4-6)
- 3 stub tools ready to replace: `web_search`, `send_email`, `create_file`
- Human-in-the-loop tool approval queue — every tool call requires explicit Approve/Reject before executing
- Supabase persistence: sessions, tool_approvals, users, usage tables (schema.sql included)
- Supabase Realtime — approval UI updates instantly via postgres_changes subscription, no polling
- Stripe billing — Checkout + Customer Portal, two plans: Starter ($29/mo, 500 tool calls) and Pro ($79/mo, unlimited)
- Plan enforcement middleware — returns 402 before tool execution when limit is exceeded
- Token cost tracking — every agent response logs input/output tokens and cost_usd to usage table
- Usage API endpoint `/usage` returning monthly totals per user
- Usage widget in the frontend — current month tokens, cost, and tool calls vs plan limit
- Next.js approval UI — dark mode, Tailwind, no component library
- `.env.example` files for both apps, fully documented inline

## Architecture

```text
User prompt
    │
    ▼
FastAPI /chat endpoint
    │
    ▼
PydanticAI Agent (reasons, plans)
    │
    ▼ (tool call detected)
tool_approvals table (status: pending)
    │
    ▼ (Supabase Realtime)
Next.js /approvals UI
    │
    ├── Reject → agent receives ToolRejected, continues without tool
    │
    └── Approve → tool executes → result back to agent
                                        │
                                        ▼
                              Agent produces final response
                                        │
                              tokens + cost logged to usage table
                                        │
                                        ▼
                              Response returned to user

Stripe webhook ──► /billing/webhook ──► updates users.plan in Supabase
Middleware checks users.plan + tool_call_count before every tool execution
```

## 10-Minute Quickstart

### Prerequisites

- Python 3.12+
- Node.js 18+
- uv (`pip install uv`)
- A Supabase project
- A Stripe account with two products created (Starter + Pro)
- An Anthropic API key

### Steps

1. Clone the repo

   ```bash
   git clone https://github.com/DarthQach/agentboiler.git
   cd agentboiler
   ```

2. Set up the database

   - Open your Supabase project → SQL Editor
   - Run the contents of `schema.sql`

3. Configure the backend

   ```bash
   cp app/.env.example app/.env
   # Edit app/.env — fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY, STRIPE_* values
   ```

4. Install and run the backend

   ```bash
   uv sync
   uvicorn app.main:app --reload --port 8000
   ```

5. Configure the frontend

   ```bash
   cd frontend
   cp .env.example .env.local
   # Edit .env.local — fill in NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, NEXT_PUBLIC_API_URL
   ```

6. Install and run the frontend

   ```bash
   npm install
   npm run dev
   ```

7. Test the flow

   - Open http://localhost:3000
   - Send a prompt that triggers a tool call
   - Open http://localhost:3000/approvals — you should see a pending approval card
   - Click Approve — the agent resumes and completes the response
   - Check http://localhost:8000/usage — token count and cost should appear

✅ If the approval card appears and the agent continues after approval, everything is wired correctly.

## How to Swap the Model

- Open `app/.env`
- Change `AGENT_MODEL` to your target model identifier
- Supported values:
  - `claude-sonnet-4-6` (default)
  - `claude-opus-4-6`
  - `gpt-4o` (requires `OPENAI_API_KEY` set)
  - Any model string supported by PydanticAI
- ⚠️ Note: AgentBoiler was built and tested against PydanticAI 1.87.0. The PydanticAI API is still evolving — check their changelog before upgrading.

## How to Add a Real Tool

1. Open `app/tools/web_search.py`
2. The stub currently returns a hardcoded string. Replace the function body with your real implementation.
3. The tool signature and PydanticAI `@agent.tool` decorator stay the same — only the internals change.
4. If your tool needs new env vars, add them to `app/.env` and `app/.env.example`.
5. Restart the backend — no other changes needed. The approval queue, retry logic, and usage tracking all apply automatically to any tool registered with the agent.

> **Tool call loop protection:** If the agent retries a rejected tool more than `AGENT_MAX_TOOL_RETRIES` times (default: 3), it gives up and responds without the tool. Adjust the limit in `app/.env`.

## Project Structure

```text
agentboiler/
├── app/                    # FastAPI backend
│   ├── main.py             # App entrypoint, route definitions
│   ├── agent.py            # PydanticAI agent setup
│   ├── tools/              # Tool stubs (web_search, send_email, create_file)
│   ├── middleware.py        # Plan enforcement + tool call counting
│   ├── billing.py          # Stripe checkout, webhook, portal endpoints
│   └── .env.example
├── frontend/               # Next.js frontend
│   ├── app/                # App Router pages
│   ├── components/         # Approval card, usage widget
│   └── .env.example
├── schema.sql              # Supabase schema — run this first
├── pyproject.toml          # Python dependencies (uv)
├── .env.example            # Full variable reference for both apps
└── README.md
```

## Stripe Setup

- Create two products in Stripe Dashboard: Starter ($29/mo) and Pro ($79/mo)
- Copy each price ID into `app/.env` as `STRIPE_STARTER_PRICE_ID` and `STRIPE_PRO_PRICE_ID`
- For local development, use Stripe CLI to forward webhooks:
  `stripe listen --forward-to localhost:8000/billing/webhook`
  Copy the printed webhook signing secret into STRIPE_WEBHOOK_SECRET in app/.env.
- For production, create a webhook endpoint in the Stripe Dashboard pointing to
  `https://your-domain.com/billing/webhook`, subscribe to
  `checkout.session.completed` and `customer.subscription.deleted`,
  and copy the signing secret into STRIPE_WEBHOOK_SECRET.
- ⚠️ Webhook signature verification is enabled by default. Do not disable it — without it, anyone can spoof billing events.

## Customization

> For deeper customization — changing the system prompt, modifying approval logic, swapping Supabase for another database — see `CUSTOMIZATION.md`.
