export default {
  plugins: ["eslint", "typescript", "unicorn", "oxc", "import"],
  categories: {
    correctness: "error",
    suspicious: "error",
  },
  rules: {
    "no-debugger": "error",
    complexity: ["error", { max: 10 }],
    "max-params": ["error", { max: 5 }],
    "max-depth": ["error", { max: 4 }],
    "max-statements": ["error", { max: 50 }],
    "max-nested-callbacks": ["error", { max: 3 }],
    "import/max-dependencies": ["error", { max: 10 }],
    "import/no-unassigned-import": ["error", { allow: ["**/*.css"] }],
    "no-empty": ["error", { allowEmptyCatch: false }],
    "typescript/ban-ts-comment": "error",
    "typescript/no-explicit-any": "error",
  },
};
