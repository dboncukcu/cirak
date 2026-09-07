"""Sync the fixtures from kalfa-v2 and check cirak's own expansion against kalfa's dumps.

kalfa is the only producer of the dumps. When ../kalfa-v2 is present this script copies
src/kalfa/templates/kalfa.yaml to kalfa.yaml, the driver documents configs/dumps/01.recipe.yaml and
tidy.recipe.yaml (their include rewritten to kalfa.yaml) and the dumps configs/dumps/01.flow.yaml and
tidy.flow.yaml, verbatim. It then opens each recipe with the mock registry and compares the dump document
cirak produces with the copied flow file, section by section; nothing is written back to kalfa-v2.

    uv run python tests/fixtures/regenerate_dumps.py
"""

import json
import re
import sys
from pathlib import Path

from ruamel.yaml import YAML

FIXTURES = Path(__file__).resolve().parent
sys.path.insert(0, str(FIXTURES.parent))

from kalfa_mock import register_all  # noqa: E402

from cirak.api import analyze, effective_flow  # noqa: E402

KALFA = FIXTURES.parents[2] / "kalfa-v2"
RESERVED = {"include", "plugins", "alias", "params", "blocks", "setup", "flow"}


def targets(directory=FIXTURES):
    """Every (recipe, dump) pair a directory holds, by name."""
    return tuple((path.name, path.name.replace(".recipe.yaml", ".flow.yaml"))
                 for path in sorted(directory.glob("*.recipe.yaml")))


TARGETS = targets()


def sync_from_kalfa() -> bool:
    if not KALFA.is_dir():
        return False
    template = KALFA / "src" / "kalfa" / "templates" / "kalfa.yaml"
    if template.is_file():
        (FIXTURES / "kalfa.yaml").write_text(template.read_text())
        print("synced kalfa.yaml")
    dumps = KALFA / "configs" / "dumps"
    for recipe, dump in targets(dumps):
        text = re.sub(r"^include: \[.*\]$", "include: [kalfa.yaml]", (dumps / recipe).read_text(), count=1, flags=re.M)
        (FIXTURES / recipe).write_text(text)
        print(f"synced {recipe}")
        if (dumps / dump).is_file():
            (FIXTURES / dump).write_text((dumps / dump).read_text())
            print(f"synced {dump}")
    return True


def plain(value):
    return json.loads(json.dumps(value, sort_keys=True, default=repr))


def document_of(recipe) -> dict:
    """The dump document cirak produces for a fixture recipe, as plain data."""
    register_all()
    analysis = analyze([str(FIXTURES / recipe)])
    errors = [problem for problem in analysis.problems if problem.severity == "error"]
    if errors:
        raise SystemExit("\n".join(f"{recipe}: {problem.kind}: {problem.message}" for problem in errors))
    return {"components": {name: value for name, value in analysis.data.items() if name not in RESERVED},
            "blocks": {name: block for name, block in analysis.data["blocks"].items() if "flow" not in block},
            "flow": effective_flow(analysis.flow)}


def differences(recipe, dump) -> list[str]:
    """Sections where cirak's expansion of the recipe and kalfa's dump differ."""
    expected = YAML(typ="safe").load((FIXTURES / dump).read_text())
    produced = document_of(recipe)
    found = [f"{dump} has sections {sorted(expected)}, expected components, blocks and flow"] \
        if set(expected) != {"components", "blocks", "flow"} else []
    for section in ("components", "blocks", "flow"):
        if section in expected and plain(produced[section]) != plain(expected[section]):
            found.append(f"{dump}: section {section} differs from cirak's expansion of {recipe}")
    return found


def main() -> int:
    if not sync_from_kalfa():
        print("kalfa-v2 not found, checking the fixtures as they are")
    status = 0
    for recipe, dump in targets():
        found = differences(recipe, dump)
        for line in found:
            print(line)
        print(f"{dump}: {'differs' if found else 'equal to cirak'}")
        status = status or int(bool(found))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
