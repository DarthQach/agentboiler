import Link from "next/link";

export default function Home() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#07090d] px-6 text-slate-100">
      <Link
        href="/approvals"
        className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-200 transition hover:border-slate-500 hover:bg-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-500"
      >
        Open approvals
      </Link>
    </main>
  );
}
