import { readdir } from "node:fs/promises";
import { extname, join, relative } from "node:path";

const MEDIA_TYPES: Record<string, string> = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
};

export async function loadAssets(
  headers: Record<string, string>,
): Promise<Map<string, Response>> {
  const root = join(import.meta.dir, "..", "dist");
  const assets = new Map<string, Response>();
  async function add(directory: string): Promise<void> {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = join(directory, entry.name);
      if (entry.isDirectory()) await add(file);
      else if (entry.isFile()) {
        const path = `/${relative(root, file).replaceAll("\\", "/")}`;
        assets.set(
          path,
          new Response(await Bun.file(file).arrayBuffer(), {
            headers: {
              ...headers,
              "content-type":
                MEDIA_TYPES[extname(path)] ?? "application/octet-stream",
            },
          }),
        );
      }
    }
  }
  await add(root);
  return assets;
}
