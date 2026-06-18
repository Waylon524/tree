/// <reference types="vite/client" />

declare module "three" {
  export class Object3D {
    position: { set(x: number, y: number, z: number): void };
    rotation: { x: number; set(x: number, y: number, z: number): void };
    scale: { set(x: number, y: number, z: number): void };
    add(object: Object3D): void;
  }

  export class Material {}
  export class Group extends Object3D {}
  export class Mesh extends Object3D {
    constructor(geometry?: unknown, material?: unknown);
  }
  export class SphereGeometry {
    constructor(radius?: number, widthSegments?: number, heightSegments?: number);
  }
  export class TorusGeometry {
    constructor(
      radius?: number,
      tube?: number,
      radialSegments?: number,
      tubularSegments?: number,
    );
  }
  export class MeshStandardMaterial extends Material {
    constructor(parameters?: Record<string, unknown>);
  }
  export class MeshBasicMaterial extends Material {
    constructor(parameters?: Record<string, unknown>);
  }
  export class Light extends Object3D {}
  export class Scene extends Object3D {}
  export class Camera extends Object3D {}
  export class WebGLRenderer {}
}

declare module "three/examples/jsm/postprocessing/EffectComposer.js" {
  export class EffectComposer {}
}
