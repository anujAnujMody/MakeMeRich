/**
 * Enforces the layer rules from .claude/skills/algo-trading-project/SKILL.md:
 *   pages -> hooks/stores/components  ->  hooks -> stores/lib/api  ->  lib/api (leaf)
 * Run: yarn arch:check (see package.json). Wire into CI — this is what turns the
 * written layer rules into something a bad PR actually can't merge.
 */
module.exports = {
  forbidden: [
    {
      name: "no-component-calls-query",
      comment:
        "components/** must not call TanStack Query directly — wrap it in a hook (see algo-trading-project skill, Anti-Patterns table).",
      severity: "error",
      from: { path: "^src/components" },
      to: { path: "^node_modules/@tanstack/react-query" },
    },
    {
      name: "no-component-imports-api",
      comment:
        "components/** must not import lib/api.ts directly — call a hook instead.",
      severity: "error",
      from: { path: "^src/components" },
      to: { path: "^src/lib/api" },
    },
    {
      name: "no-page-imports-api",
      comment:
        "pages/** must not import lib/api.ts directly — go through a hook.",
      severity: "error",
      from: { path: "^src/pages" },
      to: { path: "^src/lib/api" },
    },
    {
      name: "no-page-calls-query",
      comment:
        "pages/** must not call TanStack Query directly — wrap it in a hook.",
      severity: "error",
      from: { path: "^src/pages" },
      to: { path: "^node_modules/@tanstack/react-query" },
    },
    {
      name: "no-store-imports-api",
      comment:
        "stores/** must have NO async logic — no calling lib/api.ts from a store.",
      severity: "error",
      from: { path: "^src/stores" },
      to: { path: "^src/lib/api" },
    },
    {
      name: "no-store-calls-query",
      comment: "stores/** must not depend on TanStack Query — that's server state, not client state.",
      severity: "error",
      from: { path: "^src/stores" },
      to: { path: "^node_modules/@tanstack/react-query" },
    },
    {
      name: "no-api-imports-upward",
      comment:
        "lib/api.ts is a leaf — it must not import from hooks, stores, components, or pages.",
      severity: "error",
      from: { path: "^src/lib/api" },
      to: { path: "^src/(hooks|stores|components|pages)" },
    },
    {
      name: "no-circular",
      comment: "Circular imports make the layer graph meaningless.",
      severity: "error",
      from: {},
      to: { circular: true },
    },
  ],
  options: {
    tsPreCompilationDeps: true,
    tsConfig: { fileName: "tsconfig.app.json" },
    enhancedResolveOptions: {
      exportsFields: ["exports"],
      conditionNames: ["import", "require", "node", "default"],
    },
    doNotFollow: { path: "node_modules" },
    exclude: {
      path: "^src/(mocks|test-setup\\.ts|.*/__tests__)",
    },
  },
};
