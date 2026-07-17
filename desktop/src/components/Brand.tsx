import treeAppIcon from "../../src-tauri/icons/128x128.png";

export function Brand({ heading = false }: { heading?: boolean }) {
  const content = (
    <>
      <img className="brand-icon" src={treeAppIcon} alt="" aria-hidden="true" />
      <span className="brand-name">Tree</span>
    </>
  );

  return heading ? <h1 className="brand">{content}</h1> : <span className="brand">{content}</span>;
}
