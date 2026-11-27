export function grade(score: number): string {
  if (score > 95) return "A+";
  if (score > 90) return "A";
  if (score > 85) return "B+";
  if (score > 80) return "B";
  if (score > 75) return "C+";
  if (score > 70) return "C";
  if (score > 65) return "D+";
  if (score > 60) return "D";
  if (score > 55) return "E+";
  if (score > 50) return "E";
  return "F";
}
