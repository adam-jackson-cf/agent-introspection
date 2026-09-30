import { cp, mkdir, rm } from "node:fs/promises";
import { join } from "node:path";

const root = import.meta.dir;
const dist = join(root, "dist");
const publicDir = join(root, "public");
const indexPath = join(root, "index.html");

await rm(dist, { force: true, recursive: true });
await mkdir(join(dist, "assets"), { recursive: true });

const result = await Bun.build({
  entrypoints: [join(root, "src/main.tsx")],
  outdir: join(dist, "assets"),
  naming: "[name]-[hash].[ext]",
  target: "browser",
  minify: true,
  sourcemap: "none",
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
});

if (!result.success) {
  for (const log of result.logs) console.error(log);
  process.exitCode = 1;
} else {
  const entry = result.outputs.find((output) => output.kind === "entry-point");
  if (!entry) throw new Error("dashboard entry bundle was not produced");

  const script = `/${entry.path.slice(dist.length + 1).replaceAll("\\", "/")}`;
  const index = await Bun.file(indexPath).text();
  let builtIndex = index.replace(
    /<script\s+type=["']module["']\s+src=["']\/src\/main\.tsx["']><\/script>/,
    `<script type="module" src="${script}"></script>`,
  );
  if (builtIndex === index) {
    throw new Error("index.html must contain the dashboard main module script");
  }
  const styles = result.outputs.filter((output) =>
    output.path.endsWith(".css"),
  );
  if (styles.length === 0)
    throw new Error("dashboard stylesheet was not produced");
  builtIndex = builtIndex.replace(
    "</head>",
    `${styles.map((output) => `<link rel="stylesheet" href="/${output.path.slice(dist.length + 1).replaceAll("\\", "/")}" />`).join("\n")}\n</head>`,
  );
  await Bun.write(join(dist, "index.html"), builtIndex);
  await cp(publicDir, dist, { recursive: true });
}
