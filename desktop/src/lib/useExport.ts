import { useState } from "react";
import { exportOutputs } from "../api";
import { useT } from "../i18n";
import { chooseExportDestination } from "./export";

// Shared export flow: pick a destination, copy the files, and surface a result
// message. Used by the Fruits list and the Reader.
export function useExport() {
  const t = useT();
  const [exporting, setExporting] = useState(false);
  const [message, setMessage] = useState("");
  const [ok, setOk] = useState(true);

  const exportFiles = async (names: string[]): Promise<void> => {
    setExporting(true);
    setMessage("");
    try {
      const destination = await chooseExportDestination();
      if (!destination) {
        setOk(false);
        setMessage(t("fruits.exportCancelled"));
        return;
      }
      const result = await exportOutputs(destination, names);
      setOk(result.failed.length === 0);
      setMessage(
        t("fruits.exportResult", {
          exported: result.exported.length,
          skipped: result.skipped.length,
          failed: result.failed.length,
        }),
      );
    } catch (err) {
      setOk(false);
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setExporting(false);
    }
  };

  const reportEmpty = (text: string): void => {
    setOk(false);
    setMessage(text);
  };

  return { exporting, message, ok, exportFiles, reportEmpty };
}
