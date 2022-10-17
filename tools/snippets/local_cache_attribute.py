from __future__ import annotations

import sys
from pathlib import Path

import click
try:
    import libcst as cst
    import libcst.matchers as m
except ImportError:
    click.echo("please install libcst via: pip install libcst")
    sys.exit(1)
    

TRANSLATED_COMMENT_MATCHER = m.SimpleStatementLine(
    body=[m.Assign()],
    trailing_whitespace=m.TrailingWhitespace(
        comment=m.Comment(value="# local_cache_attribute")
    ),
)


def attr_to_list(attr: cst.Attribute) -> list[str]:
    if m.matches(attr, m.Attribute(value=m.Name(), attr=m.Name())):
        return [attr.value.value, attr.attr.value]
    if m.matches(attr, m.Attribute(value=m.Attribute(), attr=m.Name())):
        return attr_to_list(attr.value) + [attr.attr.value]
    raise ValueError("unexpected attr format")


class Unrenamer(cst.CSTTransformer):
    def __init__(self, module: cst.Module, simplify: bool) -> None:
        self.renamers = []
        self.module = module
        self.simplify = simplify

    def leave_SimpleStatementLine(
        self, node: cst.SimpleStatementLine, updated: cst.CSTNode
    ) -> cst.CSTNode | None:
        if not m.matches(updated, TRANSLATED_COMMENT_MATCHER):
            return updated
        assign: cst.Assign = updated.body[0]
        name: cst.Name = assign.targets[0].target
        value_attr: cst.Attribute = assign.value
        if not isinstance(value_attr, cst.Attribute):
            raise Exception(
                f"Unexpected non-attribute assignment: {self.module.code_for_node(assign)}"
            )
        self.renamers.append((name, value_attr))

        attr_list = attr_to_list(value_attr)
        attr_str = ".".join(attr_list)
        attr_longname = "_".join(attr_list)
        orig_name = name.value

        if self.simplify and orig_name == attr_longname:
            orig_name = attr_list[-1]

        if orig_name != attr_list[-1]:
            comment_str = f"{attr_str} -> {orig_name}"
        else:
            comment_str = attr_str

        return cst.EmptyLine(
            indent=True,
            comment=cst.Comment(f"# local_cache_attribute: {comment_str}"),
        )

    def leave_Name(self, node: cst.Name, updated: cst.Name) -> cst.CSTNode:
        for old_name, attr in self.renamers:
            if updated.deep_equals(old_name):
                return attr
        return updated

    def leave_FunctionDef_body(self, node: cst.FunctionDef) -> None:
        self.renamers.clear()


class Renamer(cst.CSTTransformer):
    def __init__(self, _module: cst.Module, _simplify: bool) -> None:
        self.renamers = []
        self.name_is_keyword = None

    def leave_EmptyLine(
        self, node: cst.EmptyLine, updated: cst.EmptyLine
    ) -> cst.CSTNode:
        if not m.matches(node, m.EmptyLine(comment=m.Comment())):
            return updated

        comment = node.comment.value
        if not comment.startswith("# local_cache_attribute: "):
            return updated

        value_str = comment[len("# local_cache_attribute: ") :]
        if " -> " in value_str:
            value_str, target_str = value_str.split(" -> ", maxsplit=1)
        else:
            target_str = None
        attr = value_str.split(".")
        name = cst.Name(target_str or attr[-1])

        statement = cst.SimpleStatementLine(
            body=[
                cst.Assign(
                    targets=[cst.AssignTarget(target=name)],
                    value=self.process_attribute(attr),
                )
            ],
            trailing_whitespace=cst.TrailingWhitespace(
                whitespace=cst.SimpleWhitespace(value="  "),
                comment=cst.Comment(value="# local_cache_attribute"),
            ),
        )
        self.renamers.append((attr, name))
        return statement

    def visit_Name(self, node: cst.Name) -> None:
        if node is self.name_is_keyword:
            return
        for _, name in self.renamers:
            if node.deep_equals(name):
                raise Exception(f"Name {name.value} already exists in the function")

    def visit_Arg_keyword(self, node: cst.Arg) -> None:
        self.name_is_keyword = node.keyword

    def leave_Arg_keyword(self, node: cst.Arg) -> None:
        self.name_is_keyword = None

    def process_attribute(self, node: list[str]) -> cst.BaseExpression:
        assert node
        if len(node) == 1:
            return cst.Name(value=node[0])
        for old_attr, name in self.renamers:
            if node == old_attr:
                return name
        return cst.Attribute(
            value=self.process_attribute(node[:-1]), attr=cst.Name(value=node[-1])
        )

    def visit_Attribute(self, node: cst.Attribute) -> bool:
        # prevent recursing into attribute chains so that we can recurse manually
        # in leave_attribute
        return False

    def leave_Attribute(
        self, node: cst.Attribute, updated: cst.Attribute
    ) -> cst.CSTNode:
        assert node.deep_equals(updated)
        try:
            return self.process_attribute(attr_to_list(updated))
        except ValueError:
            return updated

    def leave_FunctionDef_body(self, node: cst.FunctionDef) -> None:
        self.renamers.clear()


def transform_file(
    path: Path, transformer: type[cst.CSTTransformer], simplify: bool
) -> None:
    try:
        module = cst.parse_module(path.read_text())
        modified = module.visit(transformer(module, simplify))
        if modified.code != module.code:
            path.write_text(modified.code)
            click.echo(f"Successfully converted {path}")
    except Exception as e:
        click.echo(f"Failed to convert {path}: {e}")


@click.command()
@click.argument(
    "filename", nargs=-1, type=click.Path(exists=True, file_okay=True, dir_okay=True)
)
@click.option("-r", "--reverse", is_flag=True)
@click.option("-s", "--simplify", is_flag=True)
def main(filename: list[str], reverse: bool, simplify: bool) -> None:
    if not filename:
        raise click.ClickException("No files specified")

    if reverse:
        transformer = Unrenamer
    else:
        transformer = Renamer

    for name in filename:
        path = Path(name)
        if path.is_dir():
            for subpath in path.glob("**/*.py"):
                transform_file(subpath, transformer, simplify)
        else:
            transform_file(path, transformer, simplify)


if __name__ == "__main__":
    main()
