export interface Point3D {
  x: number;
  y: number;
  z: number;
}

export const FRUIT_FOCUS_DISTANCE = 220;

const FALLBACK_DIRECTION: Point3D = { x: 0.3, y: 0.15, z: 1 };
const EPSILON = 1e-6;

function finite(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

export function fixedFocusPosition(
  camera: Point3D,
  target: Point3D,
  distance = FRUIT_FOCUS_DISTANCE,
): Point3D {
  const tx = finite(target.x);
  const ty = finite(target.y);
  const tz = finite(target.z);
  let dx = finite(camera.x) - tx;
  let dy = finite(camera.y) - ty;
  let dz = finite(camera.z) - tz;
  let length = Math.hypot(dx, dy, dz);

  if (length <= EPSILON) {
    dx = FALLBACK_DIRECTION.x;
    dy = FALLBACK_DIRECTION.y;
    dz = FALLBACK_DIRECTION.z;
    length = Math.hypot(dx, dy, dz);
  }

  const safeDistance = Number.isFinite(distance) && distance > 0 ? distance : FRUIT_FOCUS_DISTANCE;
  const scale = safeDistance / length;
  return {
    x: tx + dx * scale,
    y: ty + dy * scale,
    z: tz + dz * scale,
  };
}
