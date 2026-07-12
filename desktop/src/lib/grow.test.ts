import assert from "node:assert/strict";
import { describe, it } from "node:test";
import type { ExtensionState } from "../api";
import type { Status } from "../types";
import { getGrowBlockReason } from "./grow.ts";

const readyExtension: ExtensionState = {
  installed: true,
  status: "installed",
  phase: "installed",
  progress: 100,
  message: "",
  model: "cached",
  runtime: "llama-server",
};

const idleStatus: Status = {
  phase: "idle",
  message: "",
  materials: 0,
  nodes: 0,
  edges: 0,
  active: 0,
  engine: "stopped",
  embedding_server: "not found",
  embedding_backend: "llama-server",
  errors: [],
  rows: [],
};

describe("getGrowBlockReason", () => {
  it("waits for status before allowing a run", () => {
    assert.equal(getGrowBlockReason(null, readyExtension), "loading");
  });

  it("requires the embedding extension before materials", () => {
    assert.equal(
      getGrowBlockReason(idleStatus, { ...readyExtension, installed: false }),
      "extension",
    );
  });

  it("requires imported materials", () => {
    assert.equal(getGrowBlockReason(idleStatus, readyExtension), "materials");
  });

  it("allows growth when the runtime and materials are ready", () => {
    assert.equal(getGrowBlockReason({ ...idleStatus, materials: 1 }, readyExtension), null);
  });
});
