import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { FRUIT_FOCUS_DISTANCE, fixedFocusPosition } from "./dagCamera.ts";

function distance(left: { x: number; y: number; z: number }, right: { x: number; y: number; z: number }) {
  return Math.hypot(left.x - right.x, left.y - right.y, left.z - right.z);
}

describe("fixedFocusPosition", () => {
  it("uses one fixed distance for different starting camera distances", () => {
    const target = { x: 40, y: 70, z: -15 };
    const near = fixedFocusPosition({ x: 60, y: 80, z: 40 }, target);
    const far = fixedFocusPosition({ x: 400, y: 250, z: 900 }, target);

    assert.ok(Math.abs(distance(near, target) - FRUIT_FOCUS_DISTANCE) < 1e-9);
    assert.ok(Math.abs(distance(far, target) - FRUIT_FOCUS_DISTANCE) < 1e-9);
  });

  it("preserves the current camera-to-node viewing direction", () => {
    const target = { x: 10, y: 20, z: 30 };
    const result = fixedFocusPosition({ x: 20, y: 40, z: 70 }, target, 110);
    const original = { x: 10, y: 20, z: 40 };
    const scale = 110 / Math.hypot(original.x, original.y, original.z);

    assert.deepEqual(result, {
      x: target.x + original.x * scale,
      y: target.y + original.y * scale,
      z: target.z + original.z * scale,
    });
  });

  it("uses a stable fallback when the camera overlaps the node", () => {
    const target = { x: 2, y: 3, z: 4 };
    const result = fixedFocusPosition(target, target);

    assert.ok(Number.isFinite(result.x));
    assert.ok(Number.isFinite(result.y));
    assert.ok(Number.isFinite(result.z));
    assert.ok(Math.abs(distance(result, target) - FRUIT_FOCUS_DISTANCE) < 1e-9);
  });
});
