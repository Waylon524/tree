import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  busy?: boolean;
  children: ReactNode;
}

// Thin wrapper over the existing button CSS classes. Use it for new UI; existing
// call sites can migrate over time.
export function Button({
  variant = "primary",
  busy = false,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  const variantClass = variant === "ghost" ? "ghost" : variant === "danger" ? "danger" : "";
  const classes = [variantClass, className].filter(Boolean).join(" ");
  return (
    <button type={type} className={classes || undefined} disabled={disabled || busy} {...rest}>
      {children}
    </button>
  );
}
