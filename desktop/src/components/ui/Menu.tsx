import { useState } from "react";

export interface MenuItem {
  key: string;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
}

// A small dropdown menu: a toggle button + a popover list closed by a backdrop.
// Reuses the existing menu CSS classes.
export function Menu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="tree-more">
      <button
        className="ghost tree-more-toggle"
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {label} ▾
      </button>
      {open && (
        <>
          <div className="tree-menu-backdrop" onClick={() => setOpen(false)} />
          <div className="tree-menu" role="menu">
            {items.map((item) => (
              <button
                key={item.key}
                className={`ghost${item.danger ? " uproot-item" : ""}`}
                type="button"
                disabled={item.disabled}
                onClick={() => {
                  setOpen(false);
                  item.onClick();
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
