/**
 * lurk onboard — interactive setup wizard
 *
 * Replicates the full install.sh flow as a Node CLI:
 *   1. Pre-flight checks (macOS, Python 3.11+, Swift, pipx)
 *   2. Clone or update source
 *   3. Build Swift daemon
 *   4. Install Python CLI via pipx
 *   5. Configure launchd + start daemon
 *   6. Prompt for Accessibility permission
 *   7. Print success + optional extras
 */

const { execSync, spawnSync } = require("child_process");
const path = require("path");
const os = require("os");
const fs = require("fs");
const readline = require("readline");

const HOME = os.homedir();
const LURK_DIR = path.join(HOME, ".lurk");
const LURK_SRC = path.join(LURK_DIR, "src");
const LOCAL_BIN = path.join(HOME, ".local", "bin");
const REPO_URL = "https://github.com/lurk-cli/lurk.git";

// ── Formatting ──────────────────────────────────────────────────

const RED = "\x1b[31m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const CYAN = "\x1b[36m";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";

const ok = (msg) => console.log(`  ${GREEN}\u2713${RESET} ${msg}`);
const warn = (msg) => console.log(`  ${YELLOW}!${RESET} ${msg}`);
const fail = (msg) => console.log(`  ${RED}\u2717${RESET} ${msg}`);
const step = (msg) => console.log(`\n${BOLD}${msg}${RESET}`);

// ── Helpers ─────────────────────────────────────────────────────

function run(cmd, opts = {}) {
  try {
    return execSync(cmd, {
      encoding: "utf-8",
      stdio: opts.stdio ?? "pipe",
      env: { ...process.env, PATH: `${LOCAL_BIN}:${process.env.PATH}` },
      ...opts,
    }).trim();
  } catch (e) {
    if (opts.throws !== false) throw e;
    return null;
  }
}

function which(bin) {
  try {
    return execSync(`command -v ${bin}`, { encoding: "utf-8" }).trim();
  } catch {
    return null;
  }
}

function prompt(question) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.trim().toLowerCase());
    });
  });
}

// ── Onboard ─────────────────────────────────────────────────────

async function onboard(args) {
  const flags = new Set(args);
  const installDaemon = flags.has("--install-daemon");

  console.log(
    `\n${BOLD}lurk${RESET} ${DIM}— context broker for AI tools${RESET}\n`,
  );

  // ── Step 1: Pre-flight ──

  step("Checking requirements...");

  // macOS
  if (process.platform !== "darwin") {
    fail("lurk only supports macOS.");
    process.exit(1);
  }
  ok("macOS");

  // Python 3.11+
  if (!which("python3")) {
    fail("Python 3 not found.");
    console.log("  Install it with: brew install python@3.11");
    process.exit(1);
  }

  const pyVersion = run(
    "python3 -c 'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")'",
  );
  const [pyMajor, pyMinor] = pyVersion.split(".").map(Number);

  if (pyMajor < 3 || (pyMajor === 3 && pyMinor < 11)) {
    fail(`Python 3.11+ required (found ${pyVersion}).`);
    console.log("  Install it with: brew install python@3.11");
    process.exit(1);
  }
  ok(`Python ${pyVersion}`);

  // Swift
  if (!which("swift")) {
    fail("Swift not found. Install Xcode Command Line Tools:");
    console.log("  xcode-select --install");
    process.exit(1);
  }
  const swiftVersionRaw = run("swift --version 2>&1 | head -1");
  const swiftVersion = swiftVersionRaw
    .replace(/.*version\s+/, "")
    .split(" ")[0];
  ok(`Swift ${swiftVersion}`);

  // pipx
  if (!which("pipx")) {
    warn("pipx not found — installing...");
    if (which("brew")) {
      run("brew install pipx", { stdio: "pipe", throws: false }) ||
        run("python3 -m pip install --user pipx", { throws: false });
    } else {
      run("python3 -m pip install --user pipx", { throws: false });
    }
    run("python3 -m pipx ensurepath 2>/dev/null", { throws: false });
    process.env.PATH = `${LOCAL_BIN}:${process.env.PATH}`;

    if (!which("pipx")) {
      fail("pipx installation failed. Install manually: brew install pipx");
      process.exit(1);
    }
  }
  ok("pipx");

  // ── Step 2: Clone or update source ──

  step("Getting lurk source...");

  // Detect if we're inside the lurk repo already (npm package is at repo root)
  const packageRoot = path.resolve(__dirname, "..");
  const inRepo =
    fs.existsSync(path.join(packageRoot, "daemon", "Package.swift")) &&
    fs.existsSync(path.join(packageRoot, "lurk", "src", "lurk"));

  let srcDir;

  if (inRepo) {
    srcDir = packageRoot;
    ok(`Using local repo at ${srcDir}`);
  } else if (fs.existsSync(path.join(LURK_SRC, ".git"))) {
    run(`git -C "${LURK_SRC}" pull --ff-only`, { throws: false });
    srcDir = LURK_SRC;
    ok(`Updated ${LURK_SRC}`);
  } else {
    step("Cloning repository...");
    run(`git clone --depth 1 "${REPO_URL}" "${LURK_SRC}"`, {
      stdio: "inherit",
    });
    srcDir = LURK_SRC;
    ok(`Cloned to ${LURK_SRC}`);
  }

  // ── Step 3: Build Swift daemon ──

  step("Building the daemon (this may take a minute)...");

  const daemonDir = path.join(srcDir, "daemon");
  const buildResult = spawnSync("swift", ["build", "-c", "release"], {
    cwd: daemonDir,
    stdio: "inherit",
    env: process.env,
  });

  if (buildResult.status !== 0) {
    fail("Daemon build failed.");
    process.exit(1);
  }

  fs.mkdirSync(LOCAL_BIN, { recursive: true });

  const builtBinary = path.join(daemonDir, ".build", "release", "lurk-daemon");
  const destBinary = path.join(LOCAL_BIN, "lurk-daemon");
  fs.copyFileSync(builtBinary, destBinary);
  fs.chmodSync(destBinary, 0o755);
  ok(`Installed lurk-daemon to ${destBinary}`);

  // ── Step 4: Install Python CLI via pipx ──

  step("Installing lurk CLI...");

  const pipxTarget = path.join(srcDir, "lurk");
  const pipxResult = spawnSync("pipx", ["install", pipxTarget, "--force"], {
    stdio: "inherit",
    env: { ...process.env, PATH: `${LOCAL_BIN}:${process.env.PATH}` },
  });

  if (pipxResult.status !== 0) {
    fail("Failed to install lurk CLI via pipx.");
    process.exit(1);
  }
  ok("lurk CLI installed via pipx");

  // Install all optional extras
  step("Installing optional extras...");

  const extras = [
    { spec: "lurk[mcp]", label: "MCP server (Claude Code / Cursor)" },
    { spec: "lurk[http]", label: "HTTP API" },
    { spec: "lurk[llm]", label: "LLM-enhanced context" },
  ];

  for (const { spec, label } of extras) {
    const injectResult = spawnSync("pipx", ["inject", "lurk", spec], {
      stdio: "pipe",
      env: { ...process.env, PATH: `${LOCAL_BIN}:${process.env.PATH}` },
    });
    if (injectResult.status === 0) {
      ok(label);
    } else {
      warn(
        `${label} — failed to install, add later with: pipx inject lurk "${spec}"`,
      );
    }
  }

  // ── Step 5: Configure launchd + start ──

  step("Configuring lurk...");

  const lurkBin = path.join(LOCAL_BIN, "lurk");
  const lurkEnv = { ...process.env, PATH: `${LOCAL_BIN}:${process.env.PATH}` };

  spawnSync(lurkBin, ["install", "--daemon", destBinary], {
    stdio: "inherit",
    env: lurkEnv,
  });
  ok("Launch agent configured");

  spawnSync(lurkBin, ["start"], { stdio: "inherit", env: lurkEnv });

  // ── Step 6: Accessibility permission ──

  step("Accessibility permission needed");
  console.log("");
  console.log(`  lurk needs Accessibility access to read window titles.`);
  console.log(
    `  Opening System Settings — add ${CYAN}lurk-daemon${RESET} and toggle it on.`,
  );
  console.log("");

  spawnSync("open", [
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
  ]);

  console.log(
    `  After granting permission, verify with: ${CYAN}lurk status${RESET}`,
  );

  // ── Step 7: Success ──

  console.log("");
  console.log(`${GREEN}${BOLD}lurk is installed and running.${RESET}`);
  console.log("");
  console.log(`  ${CYAN}lurk status${RESET}      Check daemon status`);
  console.log(`  ${CYAN}lurk context${RESET}     See what lurk observes`);
  console.log(`  ${CYAN}lurk agents${RESET}      See active AI agents`);
  console.log("");
  console.log(`${BOLD}Connect to your AI tools:${RESET}`);
  console.log(
    `  ${CYAN}claude mcp add lurk -- lurk serve-mcp${RESET}   Claude Code`,
  );
  console.log(
    `  ${CYAN}lurk serve-http${RESET}                        HTTP API at :4141`,
  );
  console.log("");
}

module.exports = { onboard };
