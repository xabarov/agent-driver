import ReactMarkdown from "react-markdown";
import rehypeShiki from "@shikijs/rehype";
import remarkGfm from "remark-gfm";

interface MarkdownRendererProps {
  content: string;
}

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  const rehypePlugins =
    import.meta.env.MODE === "test"
      ? []
      : ([[rehypeShiki, { theme: "github-dark" }]] as any[]);

  return (
    <div className="prose prose-invert max-w-none prose-pre:rounded-lg prose-pre:border prose-pre:border-border">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
