"""Read-only Hermes launch policy without application configuration imports."""

from collections.abc import Mapping

APPROVED_TOOLSETS = ("buttonsbebe_kb", "buttonsbebe_redo", "buttonsbebe_gorgias")
CANONICAL_TOOLSETS = ",".join(APPROVED_TOOLSETS)
PROVIDER_ENVIRONMENT = frozenset({"LANG", "LC_ALL", "TERM", "OLLAMA_API_KEY", "OPENAI_API_KEY"})


def canonical_toolsets(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Hermes requires exactly the three approved read-only toolsets")
    names = [name.strip() for name in value.split(",")]
    if len(names) != len(APPROVED_TOOLSETS) or set(names) != set(APPROVED_TOOLSETS):
        raise ValueError("Hermes requires exactly the three approved read-only toolsets")
    return CANONICAL_TOOLSETS


def child_environment(source: Mapping[str, str], *, home: str, path: str) -> dict[str, str]:
    environment = {key: value for key, value in source.items() if key in PROVIDER_ENVIRONMENT}
    if home:
        environment["HOME"] = home
    if path:
        environment["PATH"] = path
    return environment
