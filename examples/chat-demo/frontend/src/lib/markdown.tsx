import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownRendererProps {
  content: string;
}

const markdownComponents: Components = {
  code({ className, children, ...props }) {
    const text = String(children).replace(/\n$/, "");
    const isBlock = (className ?? "").includes("language-") || text.includes("\n");
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
      <pre className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-3 text-sm">
        <code className={className} {...props}>
          {text}
        </code>
      </pre>
    );
  },
  pre({ children }) {
    return <>{children}</>;
  },
};

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <div className="prose prose-invert max-w-none prose-pre:rounded-lg prose-pre:border prose-pre:border-border">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
