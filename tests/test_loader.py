from cirak.loader import load


def kinds(problems):
    return [problem.kind for problem in problems]


def test_plain_load_and_provenance(write):
    path = write("a.yaml", "alias:\n  fast: /series/statlib/rolling_mean\nparams:\n  n: 3\n")
    files, problems = load([path])
    assert problems == []
    assert len(files) == 1
    loaded = files[0]
    assert loaded.data["alias"]["fast"] == "/series/statlib/rolling_mean"
    assert loaded.provenance[("alias",)].line == 1
    assert loaded.provenance[("alias", "fast")].line == 2
    assert loaded.provenance[("params", "n")].line == 4


def test_parse_error(write):
    path = write("bad.yaml", "a: [1,\n")
    files, problems = load([path])
    assert files == []
    assert kinds(problems) == ["parse_error"]
    assert problems[0].file == path


def test_duplicate_key(write):
    path = write("dup.yaml", "a: 1\na: 2\n")
    files, problems = load([path])
    assert files == []
    assert kinds(problems) == ["duplicate_key"]


def test_top_level_must_be_mapping(write):
    path = write("list.yaml", "- 1\n- 2\n")
    files, problems = load([path])
    assert kinds(problems) == ["parse_error"]


def test_bool_keys_become_python_bools(write):
    path = write("b.yaml",
                 "flow:\n  pick:\n    branch:\n      decide: /c/p/d\n      cases:\n"
                 "        true: {uri: /a/b/c}\n        false: {uri: /a/b/d}\n")
    files, problems = load([path])
    assert problems == []
    cases = files[0].data["flow"]["pick"]["branch"]["cases"]
    assert set(cases) == {True, False}


def test_include_relative_and_stripped(write):
    write("sub/extra.yaml", "params:\n  n: 3\n")
    main = write("main.yaml", "include:\n  - sub/extra.yaml\nalias:\n  a: /x/y/z\n")
    files, problems = load([main])
    assert problems == []
    assert len(files) == 2
    by_name = {loaded.file.rsplit("/", 1)[-1]: loaded for loaded in files}
    assert "include" not in by_name["main.yaml"].data
    assert by_name["extra.yaml"].data["params"]["n"] == 3


def test_diamond_include_loads_once(write):
    write("common.yaml", "params:\n  n: 3\n")
    write("b.yaml", "include: [common.yaml]\n")
    write("c.yaml", "include: [common.yaml]\n")
    main = write("a.yaml", "include: [b.yaml, c.yaml]\n")
    files, problems = load([main])
    assert problems == []
    assert len(files) == 4


def test_include_cycle(write):
    first = write("x.yaml", "include: [y.yaml]\n")
    write("y.yaml", "include: [x.yaml]\n")
    files, problems = load([first])
    assert "include_cycle" in kinds(problems)


def test_fragment_include_needs_registry(write):
    main = write("m.yaml", "include: [/flow/proj/report]\n")
    files, problems = load([main])
    assert kinds(problems) == ["include_not_found"]


def test_missing_file(tmp_path):
    files, problems = load([str(tmp_path / "nope.yaml")])
    assert files == []
    assert kinds(problems) == ["include_not_found"]
