import { useState } from "react";
import { Button } from "./Button";

interface ConfirmByNameProps {
  title: string;
  hint: string;
  expectedName: string;
  placeholder: string;
  confirmLabel: string;
  busyLabel: string;
  cancelLabel: string;
  busy: boolean;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

// A "type the name to confirm" destructive-action panel. The confirm button is
// only enabled once the typed value matches the expected name.
export function ConfirmByName({
  title,
  hint,
  expectedName,
  placeholder,
  confirmLabel,
  busyLabel,
  cancelLabel,
  busy,
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmByNameProps) {
  const [value, setValue] = useState("");
  const matched = value.trim() === expectedName;
  return (
    <div className="tree-uproot">
      <h3>{title}</h3>
      <p className="muted">{hint}</p>
      <input
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder}
        aria-label={title}
      />
      <div className="tree-actions">
        <Button
          variant={danger ? "danger" : "primary"}
          busy={busy}
          disabled={!matched}
          onClick={onConfirm}
        >
          {busy ? busyLabel : confirmLabel}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          {cancelLabel}
        </Button>
      </div>
    </div>
  );
}
