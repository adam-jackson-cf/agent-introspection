const counts: Record<string, number> = { a: 1 };

export const total: number = (counts["a"] ?? 0) + 1;
