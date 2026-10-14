"use client";

import { Children, isValidElement, memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components, type Options } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import "./markdown.css";
import { citationLabel, citationNumberFromHref, findCitation } from "@/lib/chat/citations";
import { codeBlockKind, fenceLanguage, prepareMarkdown } from "@/lib/chat/markdown";
import type { Citation } from "@/lib/chat/types";
import MermaidBlock from "./MermaidBlock";

const REMARK_PLUGINS = [remarkGfm, remarkMath];
const REHYPE_PLUGINS: NonNullable<Options["rehypePlugins"]> = [
  [rehypeKatex, { throwOnError: false, strict: false }],
];

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) return nodeText((node.props as { children?: ReactNode }).children);
  return "";
}

/**
 * Follow `value` at most once per animation frame while `active`, so bursts of stream frames
 * cause one markdown parse per paint instead of one per frame. Inactive -> always current.
 */
function useFrameThrottled<T>(value: T, active: boolean): T {
  const [throttled, setThrottled] = useState(value);
  const latest = useRef(value);
  const raf = useRef<number | null>(null);

  useEffect(() => {
    latest.current = value;
    if (!active) return;
    if (raf.current !== null) return;
    raf.current = requestAnimationFrame(() => {
      raf.current = null;
      setThrottled(latest.current);
    });
  }, [value, active]);

  useEffect(
    () => () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
    },
    [],
  );

  return active ? throttled : value;
}

interface Props {
  content: string;
  streaming: boolean;
  citations: readonly Citation[];
  onCitationClick?: (n: number) => void;
}

function MarkdownContentImpl({ content, streaming, citations, onCitationClick }: Props) {
  const shown = useFrameThrottled(content, streaming);
  const source = useMemo(
    () => prepareMarkdown(shown, { streaming, citationCount: citations.length }),
    [shown, streaming, citations.length],
  );

  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        const n = citationNumberFromHref(href);
        if (n !== null) {
          const citation = findCitation(citations, n);
          return (
            <sup>
              <button
                type="button"
                onClick={() => onCitationClick?.(n)}
                title={citation ? citationLabel(citation) : `Source ${n}`}
                className="mx-0.5 rounded bg-blue-100 px-1 text-[0.7rem] font-medium text-blue-700 no-underline hover:bg-blue-200 dark:bg-blue-900/50 dark:text-blue-300 dark:hover:bg-blue-800/60"
              >
                {n}
              </button>
            </sup>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
      pre({ children }) {
        const child = Children.toArray(children)[0];
        if (isValidElement(child)) {
          const props = child.props as { className?: string; children?: ReactNode };
          const language = fenceLanguage(props.className);
          if (codeBlockKind(language, streaming) === "mermaid") {
            return <MermaidBlock code={nodeText(props.children).replace(/\n$/, "")} />;
          }
        }
        return <pre>{children}</pre>;
      },
    }),
    [citations, onCitationClick, streaming],
  );

  return (
    <div className="chat-md text-slate-800 dark:text-slate-100">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={components}
        disallowedElements={["img"]}
        unwrapDisallowed
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}

const MarkdownContent = memo(MarkdownContentImpl);
export default MarkdownContent;
