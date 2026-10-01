import { cp, mkdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { swapDist } from "./swap-dist";

const root = import.meta.dir;
const dist = join(root, "dist");
const publicDir = join(root, "public");
const indexPath = join(root, "index.html");

const stage = join(root, ".dist-build");
await rm(stage, { force: true, recursive: true });
await mkdir(join(stage, "assets"), { recursive: true });

const result = await Bun.build({
  entrypoints: [join(root, "src/main.tsx")],
  outdir: join(stage, "assets"),
  naming: "[name]-[hash].[ext]",
  target: "browser",
  minify: true,
  sourcemap: "none",
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
});

if (!result.success) {
  await rm(stage, { force: true, recursive: true });
  for (const log of result.logs) console.error(log);
  process.exitCode = 1;
} else {
  try {
    const entry = result.outputs.find(
      (output) => output.kind === "entry-point",
    );
    if (!entry) throw new Error("dashboard entry bundle was not produced");

    const rel = (path: string) =>
      `/${path.slice(stage.length + 1).replaceAll("\\", "/")}`;
    const index = await Bun.file(indexPath).text();
    let builtIndex = index.replace(
      /<script\s+type=["']module["']\s+src=["']\/src\/main\.tsx["']><\/script>/,
      `<script type="module" src="${rel(entry.path)}"></script>`,
    );
    if (builtIndex === index) {
      throw new Error(
        "index.html must contain the dashboard main module script",
      );
    }
    const styles = result.outputs.filter((output) =>
      output.path.endsWith(".css"),
    );
    if (styles.length === 0)
      throw new Error("dashboard stylesheet was not produced");
    if (!builtIndex.includes("</head>"))
      throw new Error("index.html must contain a closing </head> tag");
    builtIndex = builtIndex.replace(
      "</head>",
      `${styles.map((output) => `<link rel="stylesheet" href="${rel(output.path)}" />`).join("\n")}\n</head>`,
    );
    await Bun.write(join(stage, "index.html"), builtIndex);
    await cp(publicDir, stage, { recursive: true });
    await swapDist(stage, dist);
  } catch (error) {
    await rm(stage, { force: true, recursive: true });
    throw error;
  }
}
