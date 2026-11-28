import { afterEach } from "bun:test";
import childProcess from "node:child_process";
import net from "node:net";

const RULE = "TEST.t1-isolation";
const violations: string[] = [];

function block(name: string): never {
  const message = `${RULE}: a T1 bun test called ${name}; pass an owned adapter into the code under test, or move the test to a T2 job`;
  violations.push(message);
  throw new Error(message);
}

const bun = Bun as unknown as Record<string, unknown>;
const global = globalThis as unknown as Record<string, unknown>;
const processes = childProcess as unknown as Record<string, unknown>;
const sockets = net as unknown as Record<string, unknown>;

const guarded: Array<[Record<string, unknown>, string, string]> = [
  [bun, "spawn", "Bun.spawn"],
  [bun, "spawnSync", "Bun.spawnSync"],
  [bun, "serve", "Bun.serve"],
  [bun, "connect", "Bun.connect"],
  [bun, "listen", "Bun.listen"],
  [bun, "udpSocket", "Bun.udpSocket"],
  [global, "fetch", "fetch"],
  [global, "WebSocket", "WebSocket"],
  [processes, "spawn", "child_process.spawn"],
  [processes, "spawnSync", "child_process.spawnSync"],
  [processes, "exec", "child_process.exec"],
  [processes, "execSync", "child_process.execSync"],
  [processes, "execFile", "child_process.execFile"],
  [processes, "execFileSync", "child_process.execFileSync"],
  [processes, "fork", "child_process.fork"],
  [sockets, "connect", "net.connect"],
  [sockets, "createConnection", "net.createConnection"],
  [sockets, "createServer", "net.createServer"],
];

for (const [owner, key, name] of guarded) {
  owner[key] = () => block(name);
}

afterEach(() => {
  if (violations.length > 0) {
    const reported = violations.splice(0).join("\n");
    throw new Error(reported);
  }
});
