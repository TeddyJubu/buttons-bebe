const APPROVED_TOOLSETS = Object.freeze(["buttonsbebe_kb", "buttonsbebe_redo", "buttonsbebe_gorgias"]);
const CANONICAL_TOOLSETS = APPROVED_TOOLSETS.join(",");
const PROVIDER_ENVIRONMENT = new Set(["LANG", "LC_ALL", "TERM", "OLLAMA_API_KEY", "OPENAI_API_KEY"]);

function hermesArguments(text, toolsets = CANONICAL_TOOLSETS) {
  const names = typeof toolsets === "string" ? toolsets.split(",").map(name => name.trim()) : [];
  if (names.length !== 3 || new Set(names).size !== 3 || names.some(name => !APPROVED_TOOLSETS.includes(name))) {
    throw new Error("Hermes requires exactly the three approved read-only toolsets");
  }
  return ["-t", CANONICAL_TOOLSETS, "-z", text];
}

function childEnvironment(source) {
  return {
    ...Object.fromEntries(Object.entries(source).filter(([key]) => PROVIDER_ENVIRONMENT.has(key))),
    HOME: source.HERMES_OS_HOME || "/root",
    PATH: source.HERMES_PATH || "/root/.local/bin:/usr/local/bin:/usr/bin:/bin",
  };
}

module.exports = { hermesArguments, childEnvironment };
