import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Message bodies are LLM-authored markdown. react-markdown escapes raw HTML,
    so untrusted agent output stays inert. */
export default function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
