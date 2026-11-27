export abstract class Store {
  abstract load(key: string): string;
}

export function pick(value: string): string;
export function pick(value: number): number;
export function pick(value: string | number): string | number {
  return value;
}

declare function external(): void;

export const handlers = { navigate: () => {} };

export class Holder {
  constructor(readonly value: string) {}
}

export function guard(value: number): number {
  if (value < 0) {
    throw new Error("negative values are not accepted");
  }
  return value;
}

external();
