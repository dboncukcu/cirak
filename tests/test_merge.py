from cirak.loader import load
from cirak.merge import merge


def kinds(problems):
    return [problem.kind for problem in problems]


def merged(paths):
    files, problems = load(paths)
    assert problems == []
    return merge(files)


def test_disjoint_merge(write):
    a = write("a.yaml", "alias:\n  x: /a/b/c\n")
    b = write("b.yaml", "alias:\n  y: /a/b/d\nparams:\n  n: 1\n")
    data, provenance, problems = merged([a, b])
    assert problems == []
    assert data["alias"] == {"x": "/a/b/c", "y": "/a/b/d"}
    assert provenance[("alias", "y")].file == b
    assert provenance[("alias", "x")].file == a


def test_conflict_even_when_equal(write):
    a = write("a.yaml", "params:\n  n: 1\n")
    b = write("b.yaml", "params:\n  n: 1\n")
    data, provenance, problems = merged([a, b])
    assert kinds(problems) == ["merge_conflict"]
    assert "a.yaml" in problems[0].message
    assert "b.yaml" in problems[0].message


def test_commutative(write):
    a = write("a.yaml", "params:\n  n: 1\n")
    b = write("b.yaml", "params:\n  m: 2\n")
    one, _, first = merged([a, b])
    two, _, second = merged([b, a])
    assert first == second == []
    assert one == two


def test_type_mismatch(write):
    a = write("a.yaml", "thing:\n  deep: 1\n")
    b = write("b.yaml", "thing: 5\n")
    _, _, problems = merged([a, b])
    assert kinds(problems) == ["type_mismatch"]


def test_list_is_leaf(write):
    a = write("a.yaml", "params:\n  xs: [1, 2]\n")
    b = write("b.yaml", "params:\n  xs: [1, 2]\n")
    _, _, problems = merged([a, b])
    assert kinds(problems) == ["merge_conflict"]


def test_plugins_pool(write):
    a = write("a.yaml", "plugins: [proj.parts, shared.parts]\n")
    b = write("b.yaml", "plugins: [shared.parts, other.parts]\n")
    data, _, problems = merged([a, b])
    assert problems == []
    assert data["plugins"] == ["proj.parts", "shared.parts", "other.parts"]
