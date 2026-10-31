// SPDX-License-Identifier: AGPL-3.0-only
import { createClient } from "@supabase/supabase-js";

// Runtime config (written by the container at start, see
// web/docker-entrypoint.d/10-runtime-config.sh) wins over build-time VITE_*
// values, so one prebuilt image works for every self-hosted domain. Cloud
// builds leave the runtime values empty and keep using the baked-in ones.
const runtime = (typeof window !== "undefined" && window.__BRAIDSCOPE_CONFIG__) || {};
const url = runtime.supabaseUrl || import.meta.env.VITE_SUPABASE_URL;
const anonKey = runtime.supabaseAnonKey || import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase = url && anonKey ? createClient(url, anonKey) : null;

export const isSupabaseConfigured = () => Boolean(supabase);
