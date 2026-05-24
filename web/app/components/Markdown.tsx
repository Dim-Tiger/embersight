"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { type ReactNode } from "react";

type MarkdownProps = {
  children: string;
  className?: string;
  inline?: boolean;
};

// Shared renderer for agent-authored text. Agents emit GitHub-flavored
// markdown (bold, headings, bullets, tables, inline code) and we want it
// to actually render in the UI instead of showing the raw "**" / "###"
// characters. The styling is tuned for the dense dark "smoke" panels —
// headings stay small, lists keep their indents, code uses an inline pill.
export function Markdown({ children, className, inline }: MarkdownProps) {
  // Empty / whitespace-only input still needs to render so streaming
  // cursors line up; ReactMarkdown handles that fine.
  const components: Components = inline ? inlineComponents : blockComponents;
  return (
    <div className={className ? `md ${className}` : "md"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

function Paragraph({ children }: { children?: ReactNode }) {
  return <p className="whitespace-pre-wrap [&:not(:first-child)]:mt-2">{children}</p>;
}

function InlineParagraph({ children }: { children?: ReactNode }) {
  // Inline mode collapses paragraphs into a single line so chat bubbles
  // and single-line summaries don't pick up extra block spacing.
  return <span className="whitespace-pre-wrap">{children}</span>;
}

const blockComponents: Components = {
  p: ({ children }) => <Paragraph>{children}</Paragraph>,
  strong: ({ children }) => (
    <strong className="font-semibold text-smoke-50">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  h1: ({ children }) => (
    <h1 className="mt-2 text-[13px] font-semibold text-smoke-50 first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-2 text-[12.5px] font-semibold text-smoke-50 first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-2 text-[12px] font-semibold uppercase tracking-wider text-ember-300 first:mt-0">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="mt-2 text-[11px] font-semibold uppercase tracking-wider text-smoke-300 first:mt-0">
      {children}
    </h4>
  ),
  ul: ({ children }) => (
    <ul className="mt-1 list-disc space-y-0.5 pl-5 marker:text-ember-400">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-1 list-decimal space-y-0.5 pl-5 marker:text-ember-400">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-ember-300 underline decoration-dotted underline-offset-2 hover:text-ember-200"
    >
      {children}
    </a>
  ),
  code: ({ className, children }) => {
    const isBlock = /language-/.test(className ?? "");
    if (isBlock) {
      return (
        <code className="block whitespace-pre-wrap rounded bg-smoke-900/80 px-2 py-1.5 font-mono text-[11px] text-smoke-200 ring-1 ring-smoke-700/60">
          {children}
        </code>
      );
    }
    return (
      <code className="rounded bg-smoke-900/70 px-1 py-0.5 font-mono text-[10.5px] text-ember-200 ring-1 ring-smoke-700/60">
        {children}
      </code>
    );
  },
  pre: ({ children }) => <pre className="mt-2 overflow-x-auto">{children}</pre>,
  blockquote: ({ children }) => (
    <blockquote className="mt-2 border-l-2 border-ember-700/60 pl-2 italic text-smoke-300">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-2 border-smoke-700/60" />,
  table: ({ children }) => (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full border-collapse text-[11px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-smoke-900/60 text-smoke-300">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="border border-smoke-700 px-1.5 py-1 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-smoke-700/60 px-1.5 py-1 align-top">
      {children}
    </td>
  ),
};

const inlineComponents: Components = {
  ...blockComponents,
  p: ({ children }) => <InlineParagraph>{children}</InlineParagraph>,
};

// Strip the most common markdown syntax for places that need plain text
// — short summary banners, truncated previews, anywhere we slice a
// narrative to N chars and would otherwise expose dangling "**" or "#".
export function stripMarkdown(input: string): string {
  return input
    .replace(/```[\s\S]*?```/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/(\*|_)(.+?)\1/g, "$2")
    .replace(/^\s{0,3}[-*+]\s+/gm, "")
    .replace(/^\s{0,3}>\s?/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}
