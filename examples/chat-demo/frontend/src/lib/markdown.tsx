import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

interface MarkdownRendererProps {
  content: string;
}

function isExternalHref(href: string | undefined): boolean {
  return href != null && /^https?:\/\//i.test(href);
}

const markdownComponents: Components = {
  a({ href, children, ...props }) {
    const external = isExternalHref(href);
    return (
      <a
        href={href}
        className="text-sky-400 underline underline-offset-2 hover:text-sky-300"
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
        <code
          className="rounded bg-muted px-1 py-0.5 font-mono text-sm"
          {...props}
        >
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
    return (
      <pre
        className="hljs-block overflow-x-auto rounded-lg border border-border bg-muted/40 p-3 text-sm"
        {...props}
      >
        {children}
      </pre>
    );
  },
};

const rehypePlugins = [
  [
    rehypeHighlight,
    {
      languages: { python, json, bash, typescript },
    },
  ],
];

const proseClassName = [
  "prose prose-sm prose-invert max-w-none",
  "prose-p:my-2 prose-headings:mt-4 prose-headings:mb-2",
  "prose-a:text-sky-400 prose-a:underline prose-a:underline-offset-2 hover:prose-a:text-sky-300",
  "prose-code:before:content-none prose-code:after:content-none",
  "prose-pre:my-2 prose-pre:border-0 prose-pre:bg-transparent prose-pre:p-0",
  "prose-table:my-2",
  "first:prose-p:mt-0 last:prose-p:mb-0",
].join(" ");

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <div className={proseClassName}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // rehype-highlight plugin tuple is valid at runtime; react-markdown types are narrow
        rehypePlugins={rehypePlugins as never}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
