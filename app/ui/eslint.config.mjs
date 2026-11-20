// The rules the code of the app's interface is held to (the same as the panel page's) (see "Code style" in the repository's CLAUDE.md).
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**"] },
  ...tseslint.configs.strict,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "one-var": ["error", "never"],                                   // one declaration per statement
      "id-length": ["error", { min: 2, exceptions: ["_", "x", "y"] }], // full names, not letters
      "no-nested-ternary": "error",
      // a value that means something is an enum (or a named constant), never a bare string in a comparison
      "no-restricted-syntax": [
        "error",
        {
          selector: "BinaryExpression[operator=/^[!=]==$/][left.operator!='typeof'] > Literal.right[raw=/^['\"]./]",
          message: "Compare against an enum member or a named constant, not a string literal.",
        },
      ],
      "prefer-const": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
    },
  },
);
