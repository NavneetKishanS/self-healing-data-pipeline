"""Jinja text prompts loaded relative to this package, independent of the working directory."""

from hashlib import sha256
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

_DIRECTORY = Path(__file__).parent / "templates"
_NAMES = ("system", "diagnose", "critique")
_INPUTS = {"system": set(), "diagnose": {"evidence"}, "critique": {"evidence", "proposal"}}
# Procedural memory is optional: the templates render nothing for it when it is None.
_OPTIONAL = {"system": set(), "diagnose": {"procedures"}, "critique": {"procedures"}}
_ENV = Environment(
    loader=FileSystemLoader(str(_DIRECTORY)),
    undefined=StrictUndefined,
    autoescape=False,  # These are text prompts, not HTML. Evidence is serialized with tojson.
)


def render(name: str, **context) -> str:
    if name not in _NAMES:
        raise ValueError("Unknown prompt name")
    missing = _INPUTS[name] - context.keys()
    if missing:
        raise UndefinedError("Missing prompt inputs: " + ", ".join(sorted(missing)))
    for key in _OPTIONAL[name]:
        context.setdefault(key, None)
    return _ENV.get_template(name + ".jinja").render(**context)


def versions() -> dict:
    return {name: sha256((_DIRECTORY / (name + ".jinja")).read_bytes()).hexdigest()
            for name in _NAMES}
