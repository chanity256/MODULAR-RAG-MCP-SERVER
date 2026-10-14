"use client";

import { useEffect, useId, useState } from "react";
import { useTheme } from "@/context/ThemeContext";

type Result = { key: string; svg: string } | { key: string; failed: true };

/**
 * Renders a mermaid diagram. The library is imported lazily so it only ships to the browser
 * when an answer actually contains a ```mermaid block; any failure falls back to the source.
 */
export default function MermaidBlock({ code }: { code: string }) {
  const { isDark } = useTheme();
  const rawId = useId();
  const [result, setResult] = useState<Result | null>(null);
  const key = `${isDark ? "d" : "l"}:${code}`;

  useEffect(() => {
    let cancelled = false;
    const domId = `mermaid-${rawId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: isDark ? "dark" : "default" });
        const { svg } = await mermaid.render(domId, code);
        if (!cancelled) setResult({ key, svg });
      } catch {
        // mermaid leaves its error graphic attached to <body> on failure.
        document.getElementById(`d${domId}`)?.remove();
        if (!cancelled) setResult({ key, failed: true });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, isDark, key, rawId]);

  if (result && result.key === key && "svg" in result) {
    return (
      <div
        role="img"
        aria-label="Mermaid diagram"
        className="my-3 overflow-x-auto rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full"
        dangerouslySetInnerHTML={{ __html: result.svg }}
      />
    );
  }
  if (result && result.key === key) {
    return (
      <pre data-mermaid-fallback>
        <code className="language-mermaid">{code}</code>
      </pre>
    );
  }
  return (
    <pre aria-busy="true">
      <code className="language-mermaid">{code}</code>
    </pre>
  );
}
