"""Static checks over every plugin, for the code the offline suite can't run.

A source's `fetch` needs the network, so most of its body — including every
error path — is never executed by the test suite. That makes one particular
mistake invisible: removing an import that a function body still uses. It
survives `import solarapp.sources.x`, survives all 354 tests, and only shows up
as a NameError in front of a user, on the rare branch that needed it.

So instead of running the code, this reads it: every global a plugin's
bytecode loads must exist in the module or in builtins.
"""
import builtins
import dis
import importlib
import pkgutil

import pytest

import solarapp.sources as sources_pkg
from solarapp.core.registry import get_registry

MODULES = [
    importlib.import_module(f"{sources_pkg.__name__}.{info.name}")
    for info in pkgutil.iter_modules(sources_pkg.__path__)
]


def _codes(code):
    yield code
    for const in code.co_consts:  # comprehensions, lambdas, nested functions
        if hasattr(const, "co_names"):
            yield from _codes(const)


def _functions(module):
    for obj in vars(module).values():
        if isinstance(obj, type):
            members = vars(obj).values()
        else:
            members = [obj]
        for member in members:
            fn = getattr(member, "__func__", member)
            if hasattr(fn, "__code__") and getattr(fn, "__module__", None) == module.__name__:
                yield fn


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_every_global_a_plugin_uses_actually_exists(module):
    missing = set()
    for fn in _functions(module):
        for code in _codes(fn.__code__):
            for ins in dis.get_instructions(code):
                if (ins.opname == "LOAD_GLOBAL"
                        and ins.argval not in vars(module)
                        and not hasattr(builtins, ins.argval)):
                    missing.add(f"{ins.argval} (in {fn.__qualname__})")
    assert not missing, f"{module.__name__} uses undefined names: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(get_registry().sources))
def test_every_plugin_declares_a_usable_set(name):
    """The declarations the rest of the app reads must be well formed."""
    src = get_registry().get(name)
    assert src.display_name and src.keywords
    assert len(src.coverage) == 2
    keys = [v.key for v in src.variables]
    assert len(keys) == len(set(keys)), f"{name} declares a duplicate variable key"
    for var in src.variables:
        assert var.label and var.keywords, f"{name}.{var.key} has no label or keywords"
        assert var.extras_for(src), f"{name}.{var.key} produces no extras"
