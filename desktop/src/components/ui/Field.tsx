import type { InputHTMLAttributes, ReactNode } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: ReactNode;
  hint?: ReactNode;
}

// A labelled text/number input. Use it for new forms; existing forms can migrate
// over time.
export function Field({ label, hint, className, ...rest }: FieldProps) {
  return (
    <label className={["ui-field", className].filter(Boolean).join(" ")}>
      <span className="ui-field-label">{label}</span>
      <input {...rest} />
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}
