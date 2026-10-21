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
import keyword
import re

_NON_IDENTIFIER_RE = re.compile(r"[^0-9a-zA-Z_]+")


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
    if keyword.iskeyword(name):
        name = f"{name}_"
    if taken is None:
        return name
    base, n = name, 2
    while name in taken:
        name = f"{base}_{n}"
        n += 1
    taken.add(name)
    return name
