"""Dependency planning with explicit cycle and missing-input diagnostics."""
from .model import BuildError, Recipe


def plan(recipes: dict[str, Recipe], selected: list[str], *, include_runtime: bool = True) -> list[Recipe]:
    """Collect transaction inputs, then order only dependencies needed to compile.

    Runtime services can depend on one another without needing one another's
    artifacts to compile. Their entire closure still belongs in the build plan.
    """
    closure: dict[str, Recipe] = {}

    def collect(name: str, chain: list[str]) -> None:
        if name not in recipes:
            raise BuildError(f"unknown package {name}; dependency chain: {' -> '.join(chain + [name])}")
        if name in closure:
            return
        closure[name] = recipes[name]
        roles = ("native", "target", "runtime") if include_runtime else ("native", "target")
        for role in roles:
            for dependency in recipes[name].dependencies.get(role, ()):
                collect(dependency, chain + [name])

    for name in selected:
        collect(name, [])
    states: dict[str, int] = {}
    ordered = []

    def visit(name: str, chain: list[str]) -> None:
        if name not in recipes:
            raise BuildError(f"unknown package {name}; dependency chain: {' -> '.join(chain + [name])}")
        if states.get(name) == 1:
            raise BuildError(f"dependency cycle: {' -> '.join(chain + [name])}")
        if states.get(name) == 2:
            return
        states[name] = 1
        recipe = recipes[name]
        for role in ("native", "target"):
            for dependency in recipe.dependencies.get(role, ()):
                visit(dependency, chain + [name])
        states[name] = 2
        ordered.append(recipe)

    for name in closure:
        visit(name, [])
    return ordered


def affected(recipes: dict[str, Recipe], changed: set[str]) -> set[str]:
    result = set(changed)
    while True:
        additions = {r.name for r in recipes.values() if any(
            dependency in result for dependencies in r.dependencies.values() for dependency in dependencies
        )}
        if additions <= result:
            return result
        result |= additions
