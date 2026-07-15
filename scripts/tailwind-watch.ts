const input = "./theme/input.css";
const output = "./blog/static/blog/style.css";
const watchedPaths = ["./theme", "./blog/templates"];

async function build() {
  const command = new Deno.Command(Deno.execPath(), {
    args: ["run", "-A", "@tailwindcss/cli", "-i", input, "-o", output],
    stdout: "inherit",
    stderr: "inherit",
  });
  const status = await command.spawn().status;

  if (!status.success) {
    console.error("Tailwind build failed; watching for the next change.");
  }
}

await build();
console.log(`Watching ${watchedPaths.join(", ")} for Tailwind changes…`);

async function snapshot(path: string, files = new Map<string, string>()) {
  for await (const entry of Deno.readDir(path)) {
    const entryPath = `${path}/${entry.name}`;

    if (entry.isDirectory) {
      await snapshot(entryPath, files);
      continue;
    }

    if (!entry.isFile) continue;
    const info = await Deno.stat(entryPath);
    files.set(entryPath, `${info.size}:${info.mtime?.getTime()}`);
  }

  return files;
}

async function currentSnapshot() {
  const files = new Map<string, string>();
  for (const path of watchedPaths) await snapshot(path, files);
  return files;
}

function changed(previous: Map<string, string>, next: Map<string, string>) {
  return previous.size !== next.size ||
    [...next].some(([path, value]) => previous.get(path) !== value);
}

// Tailwind's watcher currently fails to start an FSEvents stream under Deno on
// macOS. Polling avoids that dependency and also works with atomic editor saves.
let previous = await currentSnapshot();
let checking = false;

setInterval(async () => {
  if (checking) return;
  checking = true;

  try {
    const next = await currentSnapshot();
    if (changed(previous, next)) {
      previous = next;
      await build();
    }
  } finally {
    checking = false;
  }
}, 500);
