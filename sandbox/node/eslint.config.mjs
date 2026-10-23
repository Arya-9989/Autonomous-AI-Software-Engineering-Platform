// Used only when a generated project ships no eslint.config.*: the same rules the TypeScript
// templates configure (ESLint's and typescript-eslint's recommended sets).
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(js.configs.recommended, ...tseslint.configs.recommended);
