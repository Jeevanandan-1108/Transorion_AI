"""
field_naming.py
----------------
Turns a template's real column name (e.g. "Inv No", "SWS Notn SrNo ") into a
valid Python identifier, for the generated static Pydantic model files (see
tools/generate_template_models.py) -- a class body needs a real attribute
name, unlike the dynamic create_model() path elsewhere in this pipeline
(chunked_extraction.py's _row_model_for_chunk), which accepts the raw
column string as a dict key directly and never needs one.

The real column name is never lost -- the generator records it as the
field's Pydantic `alias`, so the wire format (what the LLM is asked for,
and what validate.py's mappers still expect) is completely unaffected by
this; only the Python-facing attribute name changes.
"""
from __future__ import annotations
import builtins
import keyword
import re

_NON_IDENTIFIER_RE = re.compile(r"[^0-9a-zA-Z_]+")

# Confirmed real failure without this check: the column "STR" (icafe's Item
# sheet) slugifies to the attribute name "str", shadowing the builtin. Every
# generated file uses `from __future__ import annotations`, so field
# annotations are plain strings resolved later, in the CLASS'S OWN
# namespace -- which by then includes "str" bound to that field's own
# FieldInfo object, not the builtin. Any LATER field whose annotation
# needs the real `str` (e.g. `Optional[float | str]`) then fails with
# "TypeError: unsupported operand type(s) for |: 'type' and 'FieldInfo'" /
# "Unable to evaluate type annotation". A field name only needs to avoid
# colliding with whatever OTHER names the class's own annotations
# reference -- str/int/float/bool/list/dict/etc. cover every type this
# module ever actually writes into an annotation (see pydantic_type_for in
# pydantic_models.py); checking all of `builtins` is simply the safe
# superset, so this can never need revisiting as that set changes.
_is_builtin_name = frozenset(vars(builtins)).__contains__


def to_python_name(real_name: str, taken: set[str] | None = None) -> str:
    """
    `real_name` -> a valid Python identifier, unique within `taken` if
    given (mutated in place: the returned name is added to it, so repeated
    calls while building one class body never collide).
    """
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
