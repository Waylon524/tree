import type { CSSProperties } from "react";

export type StageKey = "ocr" | "clean" | "cut" | "embed" | "cluster" | "link" | "noderun";

const STAGE_GLYPHS: Record<StageKey, JSX.Element> = {
  // Gather — basket
  ocr: (
    <>
      <path d="M4 9h16l-1.6 9.5a2 2 0 0 1-2 1.7H7.6a2 2 0 0 1-2-1.7L4 9Z" />
      <path d="M8 9 11 3M16 9 13 3" />
    </>
  ),
  // Sift — sieve
  clean: (
    <>
      <circle cx="12" cy="11" r="7" />
      <path d="M7 9h10M7 12h10M9 7v8M13 7v8" />
    </>
  ),
  // Sort — split seeds
  cut: (
    <>
      <path d="M12 4v6M12 14v6" />
      <path d="M8 8c-2 2-2 4 0 6M16 8c2 2 2 4 0 6" />
    </>
  ),
  // Sow — droplet into soil
  embed: (
    <>
      <path d="M12 3c3 4 4.5 6.2 4.5 8.5a4.5 4.5 0 0 1-9 0C7.5 9.2 9 7 12 3Z" />
      <path d="M3 20h18" />
    </>
  ),
  // Sprout — seedling
  cluster: (
    <>
      <path d="M12 20v-7" />
      <path d="M12 13c-1-3-3.5-4-6-4 .3 3 2.5 5 6 5Z" />
      <path d="M12 13c1-2.6 3-3.6 5.5-3.6C17.2 12 15.3 13.5 12 13Z" />
    </>
  ),
  // Branch — tree
  link: (
    <>
      <path d="M12 21v-8" />
      <path d="M12 13 7 8M12 16l4.5-4.5" />
      <circle cx="6.5" cy="7.5" r="1.6" />
      <circle cx="17" cy="11" r="1.6" />
      <circle cx="12" cy="5" r="1.8" />
    </>
  ),
  // Fruit — apple
  noderun: (
    <>
      <path d="M12 7c2.2-2 6.2-1.4 6.7 2.3.5 3.6-2.2 8.7-4.7 8.7-1 0-1.3-.5-2-.5s-1 .5-2 .5c-2.5 0-5.2-5.1-4.7-8.7C5.8 5.6 9.8 5 12 7Z" />
      <path d="M12 7c0-1.6.8-3 2.4-3.6" />
    </>
  ),
};

export function StageGlyph({ stage, size = 18 }: { stage: StageKey; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {STAGE_GLYPHS[stage]}
    </svg>
  );
}

const SWAY: CSSProperties = { transformOrigin: "center bottom" };

// Animated apple-tree scene shown on Harvest before the Fruit stage.
export function AppleTreeStage({ stage }: { stage: StageKey | null }) {
  return (
    <svg viewBox="0 0 240 200" className="apple-scene" role="img" aria-label="apple tree stage">
      <ellipse cx="120" cy="186" rx="120" ry="20" style={{ fill: "var(--bg-soil)" }} />
      <path d="M0 176h240" style={{ stroke: "var(--trunk-soft)" }} strokeWidth="1.5" opacity="0.5" />

      {stage !== "embed" && (
        <g className="tree-sway" style={SWAY}>
          <path
            d="M118 178c0-26-2-40-1-58"
            style={{ stroke: "var(--trunk)" }}
            strokeWidth="9"
            strokeLinecap="round"
            fill="none"
          />
          <path
            d="M117 132c-9-7-16-9-26-9M120 120c8-7 17-8 27-7M119 108c-2-9 1-15 0-24"
            style={{ stroke: "var(--trunk)" }}
            strokeWidth="4.5"
            strokeLinecap="round"
            fill="none"
            className={stage === "link" ? "branch-draw" : undefined}
          />
          <ellipse cx="119" cy="84" rx="54" ry="40" style={{ fill: "var(--leaf-mature)" }} opacity="0.92" />
          <ellipse cx="92" cy="96" rx="26" ry="22" style={{ fill: "var(--leaf-mid)" }} opacity="0.85" />
          <ellipse cx="150" cy="92" rx="24" ry="20" style={{ fill: "var(--leaf-young)" }} opacity="0.8" />

          {(stage === "noderun" || stage === null) &&
            [
              [98, 74],
              [134, 70],
              [118, 92],
              [150, 84],
            ].map(([cx, cy], i) => (
              <circle
                key={i}
                cx={cx}
                cy={cy}
                r="6"
                className="fruit-pop"
                style={{ fill: "var(--fruit)", animationDelay: `${i * 0.25}s` }}
              />
            ))}
        </g>
      )}

      {stage === "cluster" && (
        <g className="sprout-rise" style={{ transformOrigin: "center bottom" }}>
          <path d="M120 176v-22" style={{ stroke: "var(--leaf-mature)" }} strokeWidth="3" strokeLinecap="round" />
          <path d="M120 162c-7-2-11-6-12-12 7 0 11 4 12 9Z" style={{ fill: "var(--leaf-young)" }} />
          <path d="M120 158c6-3 10-7 16-7-1 6-6 9-16 9Z" style={{ fill: "var(--leaf-mid)" }} />
        </g>
      )}

      {stage === "embed" &&
        [60, 120, 180].map((x, i) => (
          <circle
            key={x}
            cx={x}
            cy={120}
            r="3.4"
            className="seed-drop"
            style={{ fill: "var(--trunk-soft)", animationDelay: `${i * 0.4}s` }}
          />
        ))}

      {stage === "ocr" && (
        <>
          <path
            d="M150 150h28l-3 18h-22l-3-18Z"
            style={{ fill: "none", stroke: "var(--trunk)" }}
            strokeWidth="2.5"
          />
          {[0, 1, 2].map((i) => (
            <circle
              key={i}
              cx={158 + i * 7}
              cy={70 + i * 8}
              r="5.5"
              className="apple-fall"
              style={{ fill: "var(--fruit)", animationDelay: `${i * 0.5}s` }}
            />
          ))}
        </>
      )}

      {stage === "clean" &&
        [
          [86, 150, "var(--fruit)"],
          [110, 150, "var(--wither)"],
          [134, 150, "var(--fruit)"],
          [158, 150, "var(--wither)"],
        ].map(([cx, cy, fill], i) => (
          <circle
            key={i}
            cx={cx as number}
            cy={cy as number}
            r="7"
            className={fill === "var(--wither)" ? "fruit-cull" : "fruit-keep"}
            style={{ fill: fill as string, animationDelay: `${i * 0.2}s` }}
          />
        ))}

      {stage === "cut" &&
        [80, 104, 128, 152, 176].map((x, i) => (
          <circle
            key={x}
            cx={x}
            cy={152}
            r="6.5"
            className="fruit-sort"
            style={{ fill: "var(--fruit)", animationDelay: `${i * 0.12}s` }}
          />
        ))}
    </svg>
  );
}

// Decorative orchard skyline for the Orchard (project library) header.
export function OrchardScene() {
  return (
    <svg viewBox="0 0 600 120" className="orchard-scene" role="img" aria-label="orchard" preserveAspectRatio="xMidYMax slice">
      <path d="M0 104h600" style={{ stroke: "var(--trunk-soft)" }} strokeWidth="1.5" opacity="0.5" />
      <ellipse cx="300" cy="118" rx="320" ry="22" style={{ fill: "var(--bg-soil)" }} opacity="0.7" />
      {[70, 190, 300, 410, 530].map((x, i) => (
        <g key={x} style={{ transformOrigin: "center bottom" }} className="tree-sway" opacity={i % 2 ? 0.92 : 1}>
          <path d={`M${x} 104v-26`} style={{ stroke: "var(--trunk)" }} strokeWidth="5" strokeLinecap="round" />
          <ellipse cx={x} cy={64} rx={i % 2 ? 26 : 32} ry={i % 2 ? 22 : 26} style={{ fill: i % 2 ? "var(--leaf-mid)" : "var(--leaf-mature)" }} />
          <circle cx={x - 8} cy={58} r="3.4" style={{ fill: "var(--fruit)" }} />
          <circle cx={x + 9} cy={66} r="3.4" style={{ fill: "var(--fruit)" }} />
        </g>
      ))}
    </svg>
  );
}

// Small fruit tree for a project card; fruit count reflects generated outputs.
export function FruitTreeMark({ fruits = 0, size = 56 }: { fruits?: number; size?: number }) {
  const positions: Array<[number, number]> = [
    [20, 24],
    [38, 22],
    [29, 34],
    [44, 33],
    [15, 33],
    [30, 16],
  ];
  const shown = Math.max(0, Math.min(fruits, positions.length));
  return (
    <svg width={size} height={size} viewBox="0 0 60 60" role="img" aria-label={`tree with ${fruits} fruit`}>
      <path d="M30 54v-18" style={{ stroke: "var(--trunk)" }} strokeWidth="4" strokeLinecap="round" fill="none" />
      <path d="M30 40l-8-7M30 44l9-8" style={{ stroke: "var(--trunk)" }} strokeWidth="2.5" strokeLinecap="round" fill="none" />
      <ellipse cx="30" cy="26" rx="20" ry="17" style={{ fill: "var(--leaf-mature)" }} />
      <ellipse cx="20" cy="31" rx="9" ry="8" style={{ fill: "var(--leaf-mid)" }} opacity="0.9" />
      <ellipse cx="41" cy="29" rx="8" ry="7" style={{ fill: "var(--leaf-young)" }} opacity="0.85" />
      {positions.slice(0, shown).map(([cx, cy], i) => (
        <circle key={i} cx={cx} cy={cy} r="3.1" style={{ fill: "var(--fruit)" }} />
      ))}
    </svg>
  );
}

// Seedling used by the "preparing the soil" startup gate.
export function Seedling({ size = 72 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 72 72" role="img" aria-label="seedling">
      <path d="M8 56h56" style={{ stroke: "var(--trunk-soft)" }} strokeWidth="2" strokeLinecap="round" />
      <ellipse cx="36" cy="58" rx="22" ry="6" style={{ fill: "var(--bg-soil)" }} />
      <g className="sprout-rise" style={{ transformOrigin: "center bottom" }}>
        <path d="M36 56V30" style={{ stroke: "var(--leaf-mature)" }} strokeWidth="3.5" strokeLinecap="round" />
        <path d="M36 40c-9-2-15-8-16-17 9 0 15 6 16 13Z" style={{ fill: "var(--leaf-young)" }} />
        <path d="M36 36c8-4 13-10 22-10-1 9-9 13-22 11Z" style={{ fill: "var(--leaf-mid)" }} />
      </g>
    </svg>
  );
}
