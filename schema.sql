-- Run this in the Supabase SQL editor before starting the app.

create table if not exists sessions (
    id uuid primary key default gen_random_uuid(),
    user_id text not null,
    messages jsonb not null default '[]',
    created_at timestamptz not null default now()
);

create table if not exists users (
    id uuid primary key default gen_random_uuid(),
    email text unique,
    plan text not null default 'starter',
    stripe_customer_id text,
    tool_call_count integer not null default 0,
    created_at timestamptz not null default now()
);

alter table users
    add column if not exists plan text not null default 'starter';

alter table users
    alter column email drop not null;

alter table users
    alter column plan set default 'starter';

alter table users
    add column if not exists stripe_customer_id text;

alter table users
    add column if not exists tool_call_count integer not null default 0;

alter table users
    add column if not exists tool_call_reset_at timestamptz not null default now();

create table if not exists tool_approvals (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null,
    tool_name text not null,
    tool_args jsonb not null,
    status text not null default 'pending',
    created_at timestamptz not null default now()
);

create index if not exists tool_approvals_session_id_status_idx
    on tool_approvals(session_id, status);

create table if not exists usage (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references users(id) on delete cascade,
    session_id uuid references sessions(id) on delete set null,
    model text not null,
    input_tokens int not null default 0,
    output_tokens int not null default 0,
    cost_usd numeric(10, 6) not null default 0,
    created_at timestamptz not null default now()
);

create index if not exists idx_usage_user_id_created_at on usage (user_id, created_at);

-- Required for the browser Realtime subscription in the v1 no-auth UI.
-- The page still filters by session_id client-side, but Supabase Realtime
-- needs anon SELECT visibility before it can deliver INSERT payloads.
alter table tool_approvals enable row level security;

do $$
begin
    if not exists (
        select 1
        from pg_policies
        where schemaname = 'public'
          and tablename = 'tool_approvals'
          and policyname = 'anon can read pending tool approvals for realtime'
    ) then
        create policy "anon can read pending tool approvals for realtime"
            on tool_approvals
            for select
            to anon
            using (status = 'pending');
    end if;
end
$$;
