from __future__ import annotations
import builtins
import keyword
import re

_NON_IDENTIFIER_RE = re.compile(r"[^0-9a-zA-Z_]+")

_is_builtin_name = frozenset(vars(builtins)).__contains__


def to_python_name(real_name: str, taken: set[str] | None = None) -> str:
    name = _NON_IDENTIFIER_RE.sub("_", real_name.strip()).strip("_").lower()
    if not name:
        name = "field"
    if name[0].isdigit():
        name = f"f_{name}"
    if keyword.iskeyword(name) or _is_builtin_name(name):
        name = f"{name}_"
    if taken is None:
        return name
    base, n = name, 2
    while name in taken:
        name = f"{base}_{n}"
        n += 1
    taken.add(name)
    return name
