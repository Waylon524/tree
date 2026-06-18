#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const sidecarRoot = path.resolve(process.argv[2] ?? "packaging/dist/tre-engine");
const identity = process.env.APPLE_SIGNING_IDENTITY?.trim();

if (process.platform !== "darwin") {
  console.log("[sidecar-sign] Skipping: codesign is only required on macOS.");
  process.exit(0);
}

if (!identity) {
  console.log("[sidecar-sign] Skipping: APPLE_SIGNING_IDENTITY is not set.");
  process.exit(0);
}

if (!fs.existsSync(sidecarRoot) || !fs.statSync(sidecarRoot).isDirectory()) {
  console.error(`[sidecar-sign] Sidecar root not found: ${sidecarRoot}`);
  process.exit(2);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    ...options,
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function commandOutput(command, args) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  return result.status === 0 ? result.stdout.trim() : "";
}

function walk(root) {
  const entries = [];
  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop();
    const stats = fs.lstatSync(current);
    if (stats.isSymbolicLink()) {
      continue;
    }
    if (stats.isDirectory()) {
      entries.push({ path: current, directory: true });
      for (const name of fs.readdirSync(current)) {
        stack.push(path.join(current, name));
      }
      continue;
    }
    if (stats.isFile()) {
      entries.push({ path: current, directory: false });
    }
  }
  return entries;
}

function isMacho(filePath) {
  return commandOutput("file", ["-b", filePath]).includes("Mach-O");
}

function isFrameworkTopLevelExecutable(filePath) {
  const parentName = path.basename(path.dirname(filePath));
  if (!parentName.endsWith(".framework")) {
    return false;
  }
  return path.basename(filePath) === parentName.slice(0, -".framework".length);
}

function materializeMachoSymlinks(root) {
  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop();
    const stats = fs.lstatSync(current);
    if (stats.isSymbolicLink()) {
      const target = fs.realpathSync(current);
      if (fs.statSync(target).isFile() && isMacho(target)) {
        if (isFrameworkTopLevelExecutable(current)) {
          fs.unlinkSync(current);
          console.log(`[sidecar-sign] Removed framework executable alias ${path.relative(process.cwd(), current)}`);
          continue;
        }
        const targetMode = fs.statSync(target).mode;
        fs.unlinkSync(current);
        fs.copyFileSync(target, current);
        fs.chmodSync(current, targetMode);
        console.log(`[sidecar-sign] Materialized Mach-O symlink ${path.relative(process.cwd(), current)}`);
      }
      continue;
    }
    if (!stats.isDirectory()) {
      if (stats.isFile() && isFrameworkTopLevelExecutable(current) && isMacho(current)) {
        fs.unlinkSync(current);
        console.log(`[sidecar-sign] Removed materialized framework executable alias ${path.relative(process.cwd(), current)}`);
      }
      continue;
    }
    for (const name of fs.readdirSync(current)) {
      stack.push(path.join(current, name));
    }
  }
}

function depth(filePath) {
  return path.relative(sidecarRoot, filePath).split(path.sep).length;
}

function sign(targetPath) {
  console.log(`[sidecar-sign] Signing ${path.relative(process.cwd(), targetPath)}`);
  run("codesign", [
    "--force",
    "--options",
    "runtime",
    "--timestamp",
    "--sign",
    identity,
    targetPath,
  ]);
  run("codesign", ["--verify", "--strict", "--verbose=2", targetPath]);
}

materializeMachoSymlinks(sidecarRoot);
const entries = walk(sidecarRoot);
const mainExecutable = path.join(sidecarRoot, process.platform === "win32" ? "tre-engine.exe" : "tre-engine");
const machoFiles = entries
  .filter((entry) => !entry.directory)
  .map((entry) => entry.path)
  .filter((filePath) => filePath !== mainExecutable)
  .filter(isMacho)
  .sort((a, b) => depth(b) - depth(a));

console.log(`[sidecar-sign] Signing ${machoFiles.length} Mach-O files in ${sidecarRoot}`);
for (const filePath of machoFiles) {
  sign(filePath);
}

if (fs.existsSync(mainExecutable)) {
  sign(mainExecutable);
  run("codesign", ["--verify", "--deep", "--strict", "--verbose=2", mainExecutable]);
}

console.log("[sidecar-sign] Sidecar signing complete.");
