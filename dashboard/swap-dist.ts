import { rename, rm } from "node:fs/promises";

export async function swapDist(stage: string, dist: string): Promise<void> {
  const backup = `${dist}.bak`;
  await rm(backup, { force: true, recursive: true });
  let backedUp = false;
  try {
    await rename(dist, backup);
    backedUp = true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  try {
    await rename(stage, dist);
  } catch (error) {
    if (backedUp) await rename(backup, dist);
    throw error;
  }
  await rm(backup, { force: true, recursive: true });
}
