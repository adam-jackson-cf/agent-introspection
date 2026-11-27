import { empty } from "./contract";
import { label } from "./format";

export function view(): string {
  return label(`${empty}view`);
}
