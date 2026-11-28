const GRADES: [number, string][] = [
  [90, "A"],
  [80, "B"],
  [70, "C"],
];

export function grade(score: number): string {
  return GRADES.find(([floor]) => score > floor)?.[1] ?? "F";
}
