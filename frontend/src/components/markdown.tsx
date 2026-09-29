"use client";

/**
 * Model output, rendered as Markdown.
 *
 * The model is an untrusted source, so nothing it emits is treated as markup
 * we control. rehype-sanitize runs on the parsed tree with GitHub's schema, and
 * raw HTML is never enabled - react-markdown only passes HTML through when
 * rehype-raw is added, and it deliberately is not. That is what keeps a model
 * (or a poisoned document quoting one) from injecting script or an onerror
 * handler into the page.
 *
 * Everything here is ordinary selectable text: no user-select rules, no
 * pointer-events tricks, so copying a table or a paragraph works the way it
 * does anywhere else.
 */

import { Check, Copy } from "lucide-react";
import * as React from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { cn } from "@/components/ui";

/**
 * GitHub's schema plus the one attribute the syntax classes need. Nothing that
 * can execute: no `script`, no `style`, no event handlers, no `javascript:`.
 */
const schema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code ?? []), ["className", /^language-./]],
  },
};

function CodeBlock({ children }: { children: React.ReactNode }) {
  const [copied, setCopied] = React.useState(false);
  const ref = React.useRef<HTMLPreElement>(null);

  async function copy() {
    const text = ref.current?.innerText ?? "";
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be blocked; the text is selectable either way.
    }
  }

  return (
    <div className="group relative">
      <pre
        ref={ref}
        className="overflow-x-auto rounded-lg border border-border bg-surface-2 p-3 text-xs leading-relaxed"
      >
        {children}
      </pre>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? "Copied" : "Copy code"}
        className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-1 text-xs text-muted opacity-0 transition focus-visible:opacity-100 group-hover:opacity-100 hover:text-fg"
      >
        {copied ? <Check aria-hidden className="h-3 w-3" /> : <Copy aria-hidden className="h-3 w-3" />}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div
      className={cn(
        "space-y-3 text-sm leading-relaxed text-fg",
        // Headings step down in weight rather than size, so a model that opens
        // with "# Summary" does not tower over the page.
        "[&_h1]:text-base [&_h1]:font-semibold [&_h2]:text-sm [&_h2]:font-semibold",
        "[&_h3]:text-sm [&_h3]:font-semibold [&_h4]:text-sm [&_h4]:font-medium",
        "[&_h1]:mt-4 [&_h2]:mt-4 [&_h3]:mt-3 first:[&_h1]:mt-0 first:[&_h2]:mt-0",
        "[&_ul]:list-disc [&_ol]:list-decimal [&_ul]:pl-5 [&_ol]:pl-5 [&_li]:my-0.5",
        "[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted",
        "[&_:not(pre)>code]:rounded [&_:not(pre)>code]:bg-surface-2 [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:py-0.5 [&_:not(pre)>code]:text-xs",
        "[&_hr]:border-border",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeSanitize, schema]]}
        components={{
          // Tables scroll rather than stretching the conversation column.
          table: ({ children }) => (
            <div className="w-full overflow-x-auto">
              <table className="w-full border-collapse text-xs">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="border-b border-border">{children}</thead>,
          th: ({ children, style }) => (
            <th
              style={style}
              className="whitespace-nowrap px-2 py-1.5 text-left font-semibold text-fg"
            >
              {children}
            </th>
          ),
          td: ({ children, style }) => (
            <td style={style} className="border-t border-border px-2 py-1.5 align-top">
              {children}
            </td>
          ),
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
          // A link the model produced is not a link we vouch for.
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">
              {children}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
