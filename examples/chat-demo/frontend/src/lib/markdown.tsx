import bash from "highlight.js/lib/languages/bash";
import diff from "highlight.js/lib/languages/diff";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import { Check, Copy } from "lucide-react";
import { isValidElement, useState, type ReactNode } from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { cn } from "./cn";

interface MarkdownRendererProps {
  content: string;
}

interface StreamingMarkdownRendererProps extends MarkdownRendererProps {
  unstableTailMaxChars?: number;
}

const ALLOWED_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

const ALLOWED_CODE_LANGUAGES = [
  "language-bash",
  "language-diff",
  "language-json",
  "language-js",
  "language-jsx",
  "language-markdown",
  "language-md",
  "language-python",
  "language-py",
  "language-sh",
  "language-ts",
  "language-tsx",
  "language-typescript",
  "math-display",
  "math-inline",
];

const HIGHLIGHT_CLASSES = [
  "hljs",
  "hljs-addition",
  "hljs-attr",
  "hljs-attribute",
  "hljs-built_in",
  "hljs-bullet",
  "hljs-char",
  "hljs-code",
  "hljs-comment",
  "hljs-deletion",
  "hljs-doctag",
  "hljs-emphasis",
  "hljs-formula",
  "hljs-keyword",
  "hljs-link",
  "hljs-literal",
  "hljs-meta",
  "hljs-name",
  "hljs-number",
  "hljs-operator",
  "hljs-params",
  "hljs-property",
  "hljs-punctuation",
  "hljs-quote",
  "hljs-regexp",
  "hljs-section",
  "hljs-selector-attr",
  "hljs-selector-class",
  "hljs-selector-id",
  "hljs-selector-pseudo",
  "hljs-selector-tag",
  "hljs-string",
  "hljs-strong",
  "hljs-subst",
  "hljs-symbol",
  "hljs-tag",
  "hljs-template-tag",
  "hljs-template-variable",
  "hljs-title",
  "hljs-type",
  "hljs-variable",
];

function isExternalHref(href: string | undefined): boolean {
  return href != null && /^https?:\/\//i.test(href);
}

function safeUrlTransform(url: string): string {
  if (url.startsWith("#") || url.startsWith("/")) {
    return url;
  }
  try {
    const parsed = new URL(url);
    return ALLOWED_PROTOCOLS.has(parsed.protocol) ? url : "";
  } catch {
    return "";
  }
}

function languageFromClassName(className?: string): string | undefined {
  return className
    ?.split(/\s+/)
    .find((part) => part.startsWith("language-"))
    ?.replace(/^language-/, "");
}

function codeTextFromChildren(children: unknown): string {
  if (Array.isArray(children)) {
    return children.map((child) => codeTextFromChildren(child)).join("");
  }
  if (typeof children === "string" || typeof children === "number") {
    return String(children);
  }
  if (isValidElement<{ children?: unknown }>(children)) {
    return codeTextFromChildren(children.props.children);
  }
  return "";
}

function normalizeLanguage(language: string | undefined): string {
  if (!language) {
    return "text";
  }
  if (language === "py") {
    return "python";
  }
  if (language === "sh") {
    return "bash";
  }
  if (language === "md") {
    return "markdown";
  }
  return language;
}

function codeLabel(language: string): string {
  if (language === "text") {
    return "Code";
  }
  if (language === "tsx") {
    return "TSX";
  }
  if (language === "jsx") {
    return "JSX";
  }
  return language.charAt(0).toUpperCase() + language.slice(1);
}

function CodeBlock({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const rawText = codeTextFromChildren(children).replace(/\n$/, "");
  const language = normalizeLanguage(languageFromClassName(className));
  const isPython = language === "python";

  async function copyCode() {
    if (!rawText) {
      return;
    }
    await navigator.clipboard?.writeText(rawText);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <div
      className={cn(
        "my-3 overflow-hidden rounded-lg border bg-background/80 shadow-sm",
        isPython
          ? "border-emerald-500/30 dark:border-emerald-400/25"
          : "border-border/80",
      )}
    >
      <div className="flex h-9 items-center gap-2 border-b border-border/70 bg-muted/50 px-3">
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 font-mono text-[0.65rem] font-semibold uppercase tracking-normal",
            isPython
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              : "border-border/80 bg-background/80 text-muted-foreground",
          )}
        >
          {codeLabel(language)}
        </span>
        <button
          type="button"
          className="ml-auto inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground hover:bg-background hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={copyCode}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Copy className="h-3.5 w-3.5" aria-hidden />
          )}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="hljs-block max-h-[28rem] overflow-auto bg-muted/30 p-0 text-sm">
        <code className={className}>{children}</code>
      </pre>
    </div>
  );
}

const markdownComponents: Components = {
  a({ href, children, ...props }) {
    const external = isExternalHref(href);
    if (!href) {
      return <span>{children}</span>;
    }
    return (
      <a
        href={href}
        className="text-sky-700 underline underline-offset-2 hover:text-sky-800 dark:text-sky-400 dark:hover:text-sky-300"
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
        {...props}
      >
        {children}
      </a>
    );
  },
  table({ children, ...props }) {
    return (
      <div className="my-2 overflow-x-auto">
        <table {...props}>{children}</table>
      </div>
    );
  },
  code({ className, children, ...props }) {
    const text = String(children).replace(/\n$/, "");
    const isBlock =
      Boolean(className?.includes("language-")) ||
      Boolean(className?.includes("hljs")) ||
      text.includes("\n");
    if (!isBlock) {
      return (
        <code className="rounded bg-muted px-1 py-0.5 font-mono text-sm" {...props}>
          {text}
        </code>
      );
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
  pre({ children, ...props }) {
    if (isValidElement<{ className?: string; children?: ReactNode }>(children)) {
      return (
        <CodeBlock className={children.props.className}>
          {children.props.children}
        </CodeBlock>
      );
    }
    return <pre {...props}>{children}</pre>;
  },
};

const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [
      ...(defaultSchema.attributes?.code ?? []),
      ["className", ...ALLOWED_CODE_LANGUAGES],
    ],
    div: [
      ...(defaultSchema.attributes?.div ?? []),
      ["className", "math", "math-display"],
    ],
    span: [
      ...(defaultSchema.attributes?.span ?? []),
      ["className", "math", "math-inline", ...HIGHLIGHT_CLASSES],
    ],
  },
};

const rehypePlugins = [
  [rehypeSanitize, sanitizeSchema],
  [
    rehypeKatex,
    {
      strict: false,
      throwOnError: false,
    },
  ],
  [
    rehypeHighlight,
    {
      aliases: {
        js: "typescript",
        jsx: "typescript",
        py: "python",
        sh: "bash",
        ts: "typescript",
        tsx: "typescript",
      },
      languages: { bash, diff, json, markdown, python, typescript },
    },
  ],
];

function findOpenFenceStart(content: string): number | undefined {
  const fenceRe = /^ {0,3}(```+|~~~+)/;
  let openMarker: string | undefined;
  let openStart: number | undefined;
  let offset = 0;

  for (const line of content.match(/[^\n]*(?:\n|$)/g) ?? []) {
    if (!line) {
      continue;
    }
    const match = line.match(fenceRe);
    if (match?.[1]) {
      const marker = match[1][0];
      if (!openMarker) {
        openMarker = marker;
        openStart = offset;
      } else if (openMarker === marker) {
        openMarker = undefined;
        openStart = undefined;
      }
    }
    offset += line.length;
  }

  return openMarker ? openStart : undefined;
}

function findOpenDisplayMathStart(content: string): number | undefined {
  let dollarOpenStart: number | undefined;
  for (const match of content.matchAll(/\$\$/g)) {
    dollarOpenStart = dollarOpenStart == null ? match.index : undefined;
  }
  if (dollarOpenStart != null) {
    return dollarOpenStart;
  }

  let bracketOpenStart: number | undefined;
  for (const match of content.matchAll(/\\\[|\\\]/g)) {
    if (match[0] === "\\[") {
      bracketOpenStart = match.index;
    } else {
      bracketOpenStart = undefined;
    }
  }
  return bracketOpenStart;
}

function findTrailingTableStart(content: string): number | undefined {
  if (content.endsWith("\n\n")) {
    return undefined;
  }
  const lines = content.split("\n");
  const lastLine = lines.at(-1) ?? "";
  if (!lastLine.includes("|")) {
    return undefined;
  }
  let lineStart = content.length - lastLine.length;
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index] ?? "";
    const currentStart = content.split("\n").slice(0, index).join("\n").length;
    if (!line.includes("|") || !line.trim()) {
      break;
    }
    lineStart = index === 0 ? 0 : currentStart + 1;
  }
  return lineStart;
}

function splitStreamingMarkdown(content: string): {
  stable: string;
  unstableTail: string;
} {
  const candidates = [
    findOpenFenceStart(content),
    findOpenDisplayMathStart(content),
    findTrailingTableStart(content),
  ].filter((value): value is number => value != null);
  if (!candidates.length) {
    return { stable: content, unstableTail: "" };
  }
  const splitAt = Math.min(...candidates);
  return {
    stable: content.slice(0, splitAt).trimEnd(),
    unstableTail: content.slice(splitAt),
  };
}

function StreamingPlainTail({
  content,
  maxChars,
}: {
  content: string;
  maxChars: number;
}) {
  if (!content) {
    return null;
  }
  const visible =
    content.length > maxChars ? content.slice(content.length - maxChars) : content;
  return (
    <pre className="my-2 max-h-[18rem] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-dashed border-border/80 bg-muted/25 p-3 font-sans text-sm text-foreground/85">
      {visible}
    </pre>
  );
}

export const proseClassName = [
  "prose prose-sm max-w-none dark:prose-invert",
  "prose-p:my-2 prose-headings:mt-4 prose-headings:mb-2",
  "prose-a:text-sky-700 prose-a:underline prose-a:underline-offset-2 hover:prose-a:text-sky-800",
  "dark:prose-a:text-sky-400 dark:hover:prose-a:text-sky-300",
  "prose-code:before:content-none prose-code:after:content-none",
  "prose-pre:my-2 prose-pre:border-0 prose-pre:bg-transparent prose-pre:p-0",
  "prose-table:my-2",
  "first:prose-p:mt-0 last:prose-p:mb-0",
].join(" ");

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <div className={proseClassName}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        // rehype-highlight plugin tuple is valid at runtime; react-markdown types are narrow
        rehypePlugins={rehypePlugins as never}
        components={markdownComponents}
        skipHtml
        urlTransform={safeUrlTransform}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export function StreamingMarkdownRenderer({
  content,
  unstableTailMaxChars = 4000,
}: StreamingMarkdownRendererProps) {
  const { stable, unstableTail } = splitStreamingMarkdown(content);
  return (
    <div>
      {stable ? <MarkdownRenderer content={stable} /> : null}
      <StreamingPlainTail content={unstableTail} maxChars={unstableTailMaxChars} />
    </div>
  );
}
