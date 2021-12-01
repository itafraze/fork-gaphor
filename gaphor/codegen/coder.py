"""The code generator for modeling languages.

This is the code generator for the models used by Gaphor.

In order to work with the code generator, a model should follow some convensions:

* `Profile` packages are only for profiles (excluded from generation)
* A stereotype `simpleAttribute` can be defined, which converts an association
  to a `str` attribute

The coder first write the class declarations, including attributes and enumerations.
After that, associations are filled in, including derived unions and redefines.

Notes:
* Enumerations are classes ending with "Kind". They are refered to by attributes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable
import textwrap

from gaphor import UML
from gaphor.codegen.override import Overrides
from gaphor.core.modeling import ElementFactory
from gaphor.storage import storage
from gaphor.UML.modelinglanguage import UMLModelingLanguage

log = logging.getLogger(__name__)

header = textwrap.dedent(
    """\
    # This file is generated by coder.py. DO NOT EDIT!
    # isort: skip_file
    # flake8: noqa F401,F811
    # fmt: off

    from __future__ import annotations

    from typing import Callable

    from gaphor.core.modeling.properties import (
        association,
        attribute as _attribute,
        derived,
        derivedunion,
        enumeration as _enumeration,
        redefine,
        relation_many,
        relation_one,
    )

    """
)


def class_declaration(class_: UML.Class):
    base_classes = ", ".join(
        c.name for c in sorted(bases(class_), key=lambda c: c.name)  # type: ignore[no-any-return]
    )
    return f"class {class_.name}({base_classes}):"


def variables(class_: UML.Class, overrides: Overrides | None = None):
    if class_.ownedAttribute:
        for a in sorted(class_.ownedAttribute, key=lambda a: a.name or ""):
            full_name = f"{class_.name}.{a.name}"
            if overrides and overrides.has_override(full_name):
                yield f"{a.name}: {overrides.get_type(full_name)}"
            elif a.isDerived and not a.association:
                log.warning(f"Derived attribute {full_name} has no implementation.")
            elif a.association and is_simple_type(a.type):
                yield f'{a.name}: _attribute[str] = _attribute("{a.name}", str)'
            elif a.association:
                mult = "one" if a.upper == "1" else "many"
                comment = "  # type: ignore[assignment]" if is_reassignment(a) else ""
                yield f"{a.name}: relation_{mult}[{a.type.name}]{comment}"
            elif is_enumeration(a.type):
                enum_values = ", ".join(f'"{e.name}"' for e in a.type.ownedAttribute)
                yield f'{a.name} = _enumeration("{a.name}", ({enum_values}), "{a.type.ownedAttribute[0].name}")'
            else:
                yield f'{a.name}: _attribute[{a.typeValue}] = _attribute("{a.name}", {a.typeValue}{default_value(a)})'

    if class_.ownedOperation:
        for o in sorted(class_.ownedOperation, key=lambda a: a.name or ""):
            full_name = f"{class_.name}.{o.name}"
            if overrides and overrides.has_override(full_name):
                yield f"{o.name}: {overrides.get_type(full_name)}"
            else:
                log.warning(f"Operation {full_name} has no implementation")


def associations(c: UML.Class, overrides: Overrides | None = None):
    redefinitions = []
    for a in c.ownedAttribute:
        full_name = f"{c.name}.{a.name}"
        if overrides and overrides.has_override(full_name):
            yield overrides.get_override(full_name)
        elif not a.association or is_simple_type(a.type):
            continue
        elif redefines(a):
            redefinitions.append(
                f'{full_name} = redefine({c.name}, "{a.name}", {a.type.name}, {redefines(a)})'
            )
        elif a.isDerived:
            yield f'{full_name} = derivedunion("{a.name}", {a.type.name}{lower(a)}{upper(a)})'
        else:
            yield f'{full_name} = association("{a.name}", {a.type.name}{lower(a)}{upper(a)}{composite(a)}{opposite(a)})'

    yield from redefinitions

    for a in c.ownedAttribute:
        for slot in a.appliedStereotype[:].slot:
            if slot.definingFeature.name == "subsets":
                if is_simple_type(a.type):
                    continue
                full_name = f"{c.name}.{a.name}"
                for value in slot.value.split(","):
                    d = attribute(c, value.strip())
                    if d and d.isDerived:
                        yield f"{d.owner.name}.{d.name}.add({full_name})"  # type: ignore[attr-defined]
                    elif not d:
                        log.warning(
                            f"{full_name} wants to subset {value.strip()}, but it is not defined"
                        )
                    else:
                        log.warning(
                            f"{full_name} wants to subset {value.strip()}, but it is not a derived union"
                        )


def operations(c: UML.Class, overrides: Overrides | None = None):
    if c.ownedOperation:
        for o in sorted(c.ownedOperation, key=lambda a: a.name or ""):
            full_name = f"{c.name}.{o.name}"
            if overrides and overrides.has_override(full_name):
                yield overrides.get_override(full_name)


def default_value(a):
    if a.defaultValue:
        if a.typeValue == "int":
            defaultValue = a.defaultValue.title()
        elif a.typeValue == "str":
            defaultValue = f'"{a.defaultValue}"'
        else:
            raise ValueError(
                f"Unknown default value type: {a.owner.name}.{a.name}: {a.typeValue} = {a.defaultValue}"
            )

        return f", default={defaultValue}"
    return ""


def lower(a):
    return "" if a.lowerValue in (None, "0") else f", lower={a.lowerValue}"


def upper(a):
    return "" if a.upperValue in (None, "*") else f", upper={a.upperValue}"


def composite(a):
    return ", composite=True" if a.aggregation == "composite" else ""


def opposite(a):
    return (
        f', opposite="{a.opposite.name}"'
        if a.opposite and a.opposite.name and a.opposite.class_
        else ""
    )


def order_classes(classes: Iterable[UML.Class]) -> Iterable[UML.Class]:
    seen_classes = set()

    def order(c):
        if c not in seen_classes:
            for b in bases(c):
                yield from order(b)
            yield c
            seen_classes.add(c)

    for c in classes:  # sorted(classes, key=lambda c: c.name):  # type: ignore
        yield from order(c)


def bases(c: UML.Class) -> Iterable[UML.Class]:
    for g in c.generalization:
        yield g.general
    # TODO: Add bases from extensions


def is_enumeration(c: UML.Class) -> bool:
    return c and c.name and (c.name.endswith("Kind") or c.name.endswith("Sort"))  # type: ignore[return-value]


def is_simple_type(c: UML.Class) -> bool:
    for s in UML.model.get_applied_stereotypes(c):
        if s.name == "SimpleAttribute":
            return True
    for g in c.generalization:
        if is_simple_type(g.general):
            return True
    return False


def is_reassignment(a: UML.Property) -> bool:
    def test(c: UML.Class):
        for attr in c.ownedAttribute:
            if attr.name == a.name:
                return True
        return any(test(base) for base in bases(c))

    return any(test(base) for base in bases(a.owner))  # type:ignore[arg-type]


def is_in_profile(c: UML.Class) -> bool:
    def test(p: UML.Package):
        return isinstance(p, UML.Profile) or (p.owningPackage and test(p.owningPackage))

    return test(c.owningPackage)  # type: ignore[no-any-return]


def is_in_toplevel_package(c: UML.Class, package_name: str) -> bool:
    def test(p: UML.Package):
        return (not p.owningPackage and p.name == package_name) or (
            p.owningPackage and test(p.owningPackage)
        )

    return test(c.owningPackage)  # type: ignore[no-any-return]


def redefines(a: UML.Property) -> str | None:
    slot: UML.Slot
    for slot in a.appliedStereotype[:].slot:
        if slot.definingFeature.name == "redefines":
            return slot.value  # type: ignore[no-any-return]
    return None


def attribute(c: UML.Class, name: str) -> UML.Property | None:
    for a in c.ownedAttribute:
        if a.name == name:
            return a  # type: ignore[no-any-return]
    for base in bases(c):
        p = attribute(base, name)
        if p:
            return p
    return None


def last_minute_updates(element_factory: ElementFactory) -> None:
    """Some model updates that are hard to do from Gaphor itself."""
    for prop in element_factory.select(UML.Property):
        if prop.typeValue == "String":
            prop.typeValue = "str"
        elif prop.typeValue in ("Integer", "Boolean"):
            prop.typeValue = "int"
        else:
            c: UML.Class | None = next(
                element_factory.select(
                    lambda e: isinstance(e, UML.Class) and e.name == prop.typeValue
                ),  # type: ignore[arg-type]
                None,
            )
            if c:
                prop.type = c
                del prop.typeValue


def load_model(modelfile) -> ElementFactory:
    element_factory = ElementFactory()
    uml_modeling_language = UMLModelingLanguage()
    storage.load(
        modelfile,
        element_factory,
        uml_modeling_language,
    )

    last_minute_updates(element_factory)

    return element_factory


def coder(modelfile, overrides, out):

    element_factory = load_model(modelfile)
    classes = list(
        order_classes(
            c
            for c in element_factory.select(UML.Class)
            if not (is_enumeration(c) or is_simple_type(c) or is_in_profile(c))
        )
    )

    print(header, file=out)

    for c in classes:
        if overrides and overrides.has_override(c.name):
            print(overrides.get_override(c.name), file=out)
        else:
            print(class_declaration(c), file=out)
            properties = list(variables(c, overrides))
            if properties:
                for p in properties:
                    print(f"    {p}", file=out)
            else:
                print("    pass", file=out)
        print(file=out)
        print(file=out)

    for c in classes:
        for o in operations(c, overrides):
            print(o, file=out)

    print(file=out)

    for c in classes:
        for a in associations(c, overrides):
            print(a, file=out)


def main(modelfile, overridesfile=None, outfile=None):
    overrides = Overrides(overridesfile) if overridesfile else None
    if outfile:
        with open(outfile, "w") as out:
            coder(modelfile, overrides, out)
    else:
        coder(modelfile, overrides, sys.stdout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("modelfile", type=Path, help="Gaphor model filename")
    parser.add_argument(
        "-o", dest="outfile", type=Path, help="Python data model filename"
    )
    parser.add_argument("-r", dest="overridesfile", type=Path, help="Override filename")
    args = parser.parse_args()

    main(args.modelfile, args.overridesfile, args.outfile)
