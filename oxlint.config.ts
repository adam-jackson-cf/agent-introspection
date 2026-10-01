export default {
  rules: {
    "no-debugger": "error",
    complexity: ["error", { max: 10 }],
    "max-params": ["error", { max: 5 }],
    "max-depth": ["error", { max: 4 }],
    "max-statements": ["error", { max: 50 }],
    "max-nested-callbacks": ["error", { max: 3 }],
  },
};
