import type { ReactNode } from "react";

export type MessageKind = "ok" | "error" | "success" | "hint";

const KIND_CLASS: Record<MessageKind, string> = {
  ok: "ok",
  error: "errors",
  success: "success",
  hint: "hint",
};

// A status line that maps a semantic kind to the existing CSS classes. Inline
// renders a <span> (e.g. next to a button); otherwise a block <p>.
export function Message({
  kind,
  inline = false,
  className,
  children,
}: {
  kind: MessageKind;
  inline?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const classes = [KIND_CLASS[kind], className].filter(Boolean).join(" ");
  return inline ? <span className={classes}>{children}</span> : <p className={classes}>{children}</p>;
}
