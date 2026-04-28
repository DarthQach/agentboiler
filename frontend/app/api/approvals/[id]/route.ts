import { createClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";

type ApprovalDecision = "approved" | "rejected";

function isApprovalDecision(status: unknown): status is ApprovalDecision {
  return status === "approved" || status === "rejected";
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const body = (await request.json().catch(() => null)) as { status?: unknown } | null;

  if (!body || !isApprovalDecision(body.status)) {
    return NextResponse.json({ error: "Invalid approval status." }, { status: 400 });
  }

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL;
  const secretKey = process.env.SUPABASE_SECRET_KEY;

  if (!supabaseUrl || !secretKey) {
    return NextResponse.json({ error: "Supabase is not configured." }, { status: 400 });
  }

  const supabase = createClient(supabaseUrl, secretKey, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
    },
  });

  const { data, error } = await supabase
    .from("tool_approvals")
    .update({ status: body.status })
    .eq("id", id)
    .select("id, session_id, tool_name, tool_args, status, created_at")
    .single();

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 400 });
  }

  return NextResponse.json({ approval: data }, { status: 200 });
}
