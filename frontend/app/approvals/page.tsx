"use client";

import { createClient } from "@supabase/supabase-js";
import { useEffect, useMemo, useState } from "react";
import UsageWidget from "@/components/UsageWidget";

type ApprovalStatus = "pending" | "approved" | "rejected";

type ToolApproval = {
  id: string;
  session_id: string;
  tool_name: string;
  tool_args: unknown;
  status: ApprovalStatus;
  created_at: string;
};

const SESSION_STORAGE_KEY = "agentboiler.session_id";

export default function ApprovalsPage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<ToolApproval[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatingIds, setUpdatingIds] = useState<Set<string>>(new Set());

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabasePublishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

  const supabase = useMemo(() => {
    if (!supabaseUrl || !supabasePublishableKey) {
      return null;
    }

    return createClient(supabaseUrl, supabasePublishableKey, {
      auth: {
        persistSession: false,
        autoRefreshToken: false,
        detectSessionInUrl: false,
      },
    });
  }, [supabaseUrl, supabasePublishableKey]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sessionFromUrl = params.get("session");
    const sessionFromStorage = window.localStorage.getItem(SESSION_STORAGE_KEY);
    const activeSessionId = sessionFromUrl || sessionFromStorage;

    if (sessionFromUrl) {
      window.localStorage.setItem(SESSION_STORAGE_KEY, sessionFromUrl);
    }

    setSessionId(activeSessionId);
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setApprovals([]);
      setIsLoading(false);
      setError(null);
      return;
    }

    if (!supabase) {
      setApprovals([]);
      setIsLoading(false);
      setError("Supabase is not configured.");
      return;
    }

    let isMounted = true;
    const client = supabase;
    const activeSessionId = sessionId;

    async function loadPendingApprovals() {
      setIsLoading(true);
      setError(null);

      const response = await fetch(
        `/api/approvals?session=${encodeURIComponent(activeSessionId)}`,
      );

      if (!isMounted) {
        return;
      }

      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { error?: string } | null;
        setApprovals([]);
        setError(body?.error || "Failed to load approvals.");
      } else {
        const body = (await response.json()) as { approvals?: ToolApproval[] };
        setApprovals(body.approvals ?? []);
      }

      setIsLoading(false);
    }

    void loadPendingApprovals();

    const channel = client
      .channel(`tool-approvals:${sessionId}`)
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "tool_approvals",
          filter: `session_id=eq.${sessionId}`,
        },
        (payload) => {
          const row = payload.new as ToolApproval;

          if (row.status !== "pending" || row.session_id !== sessionId) {
            return;
          }

          setApprovals((current) => {
            if (current.some((approval) => approval.id === row.id)) {
              return current;
            }

            return [...current, row];
          });
        },
      )
      .subscribe((status) => {
        if (status === "CHANNEL_ERROR") {
          setError("Realtime subscription failed.");
        }
      });

    return () => {
      isMounted = false;
      void client.removeChannel(channel);
    };
  }, [sessionId, supabase]);

  async function updateApproval(id: string, status: Exclude<ApprovalStatus, "pending">) {
    setUpdatingIds((current) => new Set(current).add(id));
    setError(null);

    try {
      const response = await fetch(`/api/approvals/${id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ status }),
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { error?: string } | null;
        throw new Error(body?.error || "Approval update failed.");
      }

      setApprovals((current) => current.filter((approval) => approval.id !== id));
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Approval update failed.");
    } finally {
      setUpdatingIds((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    }
  }

  return (
    <main className="min-h-screen bg-[#07090d] px-6 py-10 text-slate-100">
      <section className="mx-auto w-full max-w-2xl">
        <div className="mb-6">
          <UsageWidget />
        </div>

        <header className="mb-6">
          <p className="mb-2 text-sm font-medium text-zinc-500">AgentBoiler</p>
          <h1 className="text-2xl font-semibold tracking-normal text-white">Pending approvals</h1>
          {sessionId ? (
            <p className="mt-2 break-all font-mono text-xs text-slate-500">Session {sessionId}</p>
          ) : (
            <p className="mt-2 text-sm text-amber-400">No session selected.</p>
          )}
        </header>

        {error ? (
          <div className="mb-5 rounded-md border border-red-900/70 bg-red-950/30 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        {isLoading ? (
          <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
            Loading approvals...
          </div>
        ) : approvals.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-32 text-sm text-zinc-500">
            No pending approvals
          </div>
        ) : (
          <div className="grid gap-4">
            {approvals.map((approval) => {
              const isUpdating = updatingIds.has(approval.id);

              return (
                <article
                  key={approval.id}
                  className="rounded-xl border border-zinc-800 bg-zinc-900 p-5"
                >
                  <div className="mb-2 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <h2 className="mb-2 font-mono text-sm font-semibold text-white">
                        {approval.tool_name}
                      </h2>
                      <p className="mt-1 font-mono text-xs text-slate-500">{approval.id}</p>
                    </div>
                    <time className="text-xs text-slate-500" dateTime={approval.created_at}>
                      {new Date(approval.created_at).toLocaleString()}
                    </time>
                  </div>

                  <pre className="mb-4 overflow-x-auto rounded-lg bg-zinc-950 p-3 font-mono text-xs text-zinc-400">
                    {JSON.stringify(approval.tool_args, null, 2)}
                  </pre>

                  <div className="flex gap-3">
                    <button
                      type="button"
                      disabled={isUpdating}
                      onClick={() => void updateApproval(approval.id, "rejected")}
                      className="rounded-lg bg-red-700 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-600 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      Reject
                    </button>
                    <button
                      type="button"
                      disabled={isUpdating}
                      onClick={() => void updateApproval(approval.id, "approved")}
                      className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      Approve
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </main>
  );
}
