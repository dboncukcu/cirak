from dataclasses import dataclass


class CirakError(Exception):
    pass


class RegistryError(CirakError):
    pass


class ConfigError(CirakError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(render_problems(self.problems))


class BuildError(CirakError):
    pass


class CirakWarning(UserWarning):
    pass


@dataclass(frozen=True)
class Source:
    file: str
    line: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class Problem:
    severity: str
    kind: str
    message: str
    file: str | None = None
    line: int | None = None
    hint: str | None = None


def error(kind: str, message: str, source: Source | None = None, hint: str | None = None) -> Problem:
    return Problem("error", kind, message,
                   source.file if source else None,
                   source.line if source else None,
                   hint)


def warning(kind: str, message: str, source: Source | None = None, hint: str | None = None) -> Problem:
    return Problem("warning", kind, message,
                   source.file if source else None,
                   source.line if source else None,
                   hint)


def render_problems(problems: list[Problem]) -> str:
    word = "problem" if len(problems) == 1 else "problems"
    lines = [f"{len(problems)} {word} found:"]
    for number, problem in enumerate(problems, 1):
        line = f"  {number}. [{problem.kind}] {problem.message}"
        if problem.file is not None:
            line += f" ({problem.file}:{problem.line})"
        if problem.hint is not None:
            line += f"; {problem.hint}"
        lines.append(line)
    return "\n".join(lines)


def dotted(path: tuple) -> str:
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        elif text:
            text += f".{part}"
        else:
            text = str(part)
    return text
