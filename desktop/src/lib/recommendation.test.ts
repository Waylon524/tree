import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { recommendationReasonCode } from "./recommendation.ts";

describe("recommendationReasonCode", () => {
  it("accepts the structured recommendation contract", () => {
    assert.equal(recommendationReasonCode({ code: "root_ready", params: {} }), "root_ready");
  });

  it("localizes known legacy strings without exposing arbitrary text", () => {
    assert.equal(recommendationReasonCode("All prerequisite nodes have been read."), "prerequisites_read");
    assert.equal(recommendationReasonCode("provider supplied English text"), null);
  });

  it("rejects unknown or missing reason codes", () => {
    assert.equal(recommendationReasonCode({ code: "unknown" }), null);
    assert.equal(recommendationReasonCode(null), null);
  });
});
