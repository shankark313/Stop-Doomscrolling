-- One-time setup for AI Briefing config persistence.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
--
-- The app stores the whole user config as a single JSONB row (id = 'default'),
-- read on page load and upserted on every save from the settings UI.

create table if not exists app_config (
  id text primary key,
  config jsonb not null,
  updated_at timestamptz not null default now()
);

-- The app authenticates with the anon key, so RLS must allow anon access.
-- This is acceptable for a single-user app whose config holds no secrets
-- (the Telegram chat id is the most sensitive field). If you want it locked
-- down harder, use the service_role key in SUPABASE_ANON_KEY's place and
-- drop these policies.
alter table app_config enable row level security;

create policy "anon can read config" on app_config
  for select using (true);

create policy "anon can insert config" on app_config
  for insert with check (true);

create policy "anon can update config" on app_config
  for update using (true) with check (true);
