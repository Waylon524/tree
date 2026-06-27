import type { ReactNode } from "react";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={["card", className].filter(Boolean).join(" ")}>{children}</div>;
}

export function SectionHeader({
  title,
  actions,
  className,
}: {
  title: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={["section-head", className].filter(Boolean).join(" ")}>
      <h2>{title}</h2>
      {actions}
    </div>
  );
}
