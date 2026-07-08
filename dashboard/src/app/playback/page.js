"use client";

import { useRouter } from "next/navigation";
import RecordingPlayback from "../components/RecordingPlayback";

export default function PlaybackPage() {
  const router = useRouter();

  return (
    <div className="min-h-screen bg-[#08090f] text-[#c9ccd8] font-sans antialiased">
      <div className="fixed inset-0 pointer-events-none z-0">
        <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] bg-purple-900/10 rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] bg-cyan-900/10 rounded-full blur-[120px]" />
      </div>

      <header className="relative z-10 border-b border-slate-900 bg-slate-950/80 backdrop-blur-md px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="relative flex h-3 w-3">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-purple-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-3 w-3 bg-purple-500"></span>
          </div>
          <h1 className="text-xl font-bold tracking-wider text-white">Recording Playback</h1>
        </div>
        <div className="flex items-center gap-4">
          <button
            onClick={() => router.push("/")}
            className="text-xs text-slate-400 hover:text-white transition-colors px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg"
          >
            Single View
          </button>
          <button
            onClick={() => router.push("/grid")}
            className="text-xs text-slate-400 hover:text-white transition-colors px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg"
          >
            Grid View
          </button>
        </div>
      </header>

      <main className="relative z-10 p-6 max-w-[1200px] mx-auto">
        <RecordingPlayback />
      </main>
    </div>
  );
}
