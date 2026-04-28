"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type UsageResponse = {
  period: {
    month: number;
    year: number;
  };
  tokens: {
    input: number;
    output: number;
    total: number;
  };
  cost_usd: number;
  requests: number;
  tool_calls: {
    used: number;
    limit: number | null;
    remaining: number | null;
  };
  plan: string;
};

const MONTH_FORMATTER = new Intl.DateTimeFormat("en-US", {
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});
const TOKEN_FORMATTER = new Intl.NumberFormat("en-US");

function formatPeriod(month: number, year: number) {
  return MONTH_FORMATTER.format(new Date(Date.UTC(year, month - 1, 1)));
}

function formatCost(cost: number) {
  if (cost > 0 && cost < 0.01) {
    return `$${cost.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")}`;
  }

  return `$${cost.toFixed(2)}`;
}

function formatPlan(plan: string) {
  return plan.charAt(0).toUpperCase() + plan.slice(1);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isSupabaseAuthToken(value: unknown): value is { access_token: string } {
  return isRecord(value) && typeof value.access_token === "string" && value.access_token.length > 0;
}

function getSupabaseProjectRef() {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!supabaseUrl) {
    return null;
  }

  try {
    return new URL(supabaseUrl).hostname.split(".")[0] || null;
  } catch {
    return null;
  }
}

function getAccessToken() {
  const projectRef = getSupabaseProjectRef();
  if (!projectRef) {
    return null;
  }

  const storedValue = window.localStorage.getItem(`sb-${projectRef}-auth-token`);
  if (!storedValue) {
    return null;
  }

  try {
    const parsedValue: unknown = JSON.parse(storedValue);
    if (!isSupabaseAuthToken(parsedValue)) {
      return null;
    }

    return parsedValue.access_token;
  } catch {
    return null;
  }
}

function LoadingSkeleton() {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/80 p-5">
      <div className="space-y-3">
        <div className="h-4 w-40 animate-pulse rounded bg-zinc-800" />
        <div className="h-4 w-full animate-pulse rounded bg-zinc-800" />
        <div className="h-4 w-3/4 animate-pulse rounded bg-zinc-800" />
      </div>
    </div>
  );
}

export default function UsageWidget() {
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function loadUsage() {
      const accessToken = getAccessToken();
      if (!accessToken) {
        if (isMounted) {
          setError("Not authenticated. Please log in.");
          setIsLoading(false);
        }
        return;
      }

      const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

      try {
        const response = await fetch(`${apiBaseUrl}/usage`, {
          headers: {
            Authorization: `Bearer ${accessToken}`,
          },
        });

        if (!response.ok) {
          throw new Error("Usage request failed.");
        }

        const body = (await response.json()) as UsageResponse;
        if (isMounted) {
          setUsage(body);
          setError(null);
        }
      } catch {
        if (isMounted) {
          setError("Could not load usage data.");
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    }

    void loadUsage();

    return () => {
      isMounted = false;
    };
  }, []);

  if (isLoading) {
    return <LoadingSkeleton />;
  }

  if (error || !usage) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-950/80 p-5 text-sm text-red-300">
        {error ?? "Could not load usage data."}
      </div>
    );
  }

  const hasToolLimit = usage.tool_calls.limit !== null;
  const toolCallPercent = hasToolLimit
    ? Math.min((usage.tool_calls.used / Math.max(usage.tool_calls.limit ?? 1, 1)) * 100, 100)
    : 0;

  return (
    <aside className="rounded-lg border border-zinc-800 bg-zinc-950/80 p-5 shadow-sm shadow-black/20">
      <div className="mb-5 flex items-center justify-between gap-4">
        <h2 className="text-base font-semibold text-white">
          Usage &mdash; {formatPeriod(usage.period.month, usage.period.year)}
        </h2>
        <span className="text-xs text-slate-500">{usage.requests} requests</span>
      </div>

      <dl className="space-y-3 text-sm">
        <div className="flex items-center justify-between gap-4">
          <dt className="text-slate-400">Tokens used</dt>
          <dd className="font-medium text-slate-100">
            {TOKEN_FORMATTER.format(usage.tokens.total)}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-4">
          <dt className="text-slate-400">Cost this month</dt>
          <dd className="font-medium text-slate-100">{formatCost(usage.cost_usd)}</dd>
        </div>
        <div className="flex items-center justify-between gap-4">
          <dt className="text-slate-400">Tool calls</dt>
          <dd className="font-medium text-slate-100">
            {hasToolLimit
              ? `${TOKEN_FORMATTER.format(usage.tool_calls.used)} / ${TOKEN_FORMATTER.format(
                  usage.tool_calls.limit ?? 0,
                )}`
              : "Unlimited"}
          </dd>
        </div>
      </dl>

      {hasToolLimit ? (
        <div className="mt-4">
          <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
            <div
              className="h-full rounded-full bg-emerald-500 transition-[width]"
              style={{ width: `${toolCallPercent}%` }}
            />
          </div>
          <p className="mt-2 text-right text-xs text-slate-500">
            {toolCallPercent.toFixed(1)}%
          </p>
        </div>
      ) : (
        <p className="mt-4 text-sm text-emerald-300">Unlimited</p>
      )}

      <div className="mt-5 flex items-center gap-2 text-sm text-slate-400">
        <span>Plan: {formatPlan(usage.plan)}</span>
        {usage.plan === "pro" ? null : (
          <>
            <span className="text-zinc-700">·</span>
            <Link className="text-emerald-300 transition hover:text-emerald-200" href="/billing/checkout">
              Upgrade &rarr;
            </Link>
          </>
        )}
      </div>
    </aside>
  );
}
