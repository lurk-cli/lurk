#!/usr/bin/env node

/**
 * lurk CLI — npm entry point
 *
 * Handles `lurk onboard` directly in Node.
 * All other commands are forwarded to the Python `lurk` CLI.
 */

const { onboard } = require("../lib/onboard");

const args = process.argv.slice(2);
const command = args[0];

if (command === "onboard") {
  onboard(args.slice(1));
} else {
  // Forward to the Python lurk CLI
  const { spawnSync } = require("child_process");
  const result = spawnSync("lurk", args, {
    stdio: "inherit",
    env: {
      ...process.env,
      PATH: `${process.env.HOME}/.local/bin:${process.env.PATH}`,
    },
  });

  if (result.error) {
    if (result.error.code === "ENOENT") {
      console.error(
        "lurk CLI not found. Run `lurk onboard` to install everything."
      );
      process.exit(1);
    }
    throw result.error;
  }
  process.exit(result.status ?? 1);
}
