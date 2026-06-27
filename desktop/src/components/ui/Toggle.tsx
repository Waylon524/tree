import type { ReactNode } from "react";

// A checkbox + label. Reuses caller-supplied classes for layout where present.
export function Toggle({
  checked,
  onChange,
  label,
  className,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <label className={["ui-toggle", className].filter(Boolean).join(" ")}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      {label}
    </label>
  );
}
