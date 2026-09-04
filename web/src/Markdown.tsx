import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Message bodies are LLM-authored markdown. react-markdown escapes raw HTML,
    so untrusted agent output stays inert.

    Memoised, and the `children: string` type is what makes that work rather
    than a decoration on it. Every page that renders these polls, and `usePoll`
    hands back a freshly parsed array on every tick — so the *objects* holding
    these bodies are new several times a minute even when nobody has said
    anything. `memo` compares props, and this component's only prop is the
    primitive string: same text, same value, React bails out before re-entering
    remark. Without it, a boardroom of 200 messages re-parsed all 200 bodies
    every two seconds, and again on every keystroke in the composer beside them
    — markdown → mdast → hast → elements, for text that had not changed since
    yesterday.

    So: keep the prop a string. Widening it to `ReactNode`, or passing two
    children (`{a}{b}` makes `props.children` a fresh array every render),
    silently turns the memo off and nothing fails — the page just gets slow
    again. The type is the guard; `npm run build` enforces it. */
export default memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
});
