"""Calculated fields: a closed expression grammar with no host access.

PRD §6.6 requires calculated and custom fields "through safe constrained expressions,
not arbitrary code execution". That is an unusual requirement to meet convincingly,
because the obvious implementations all fail it. ``eval`` fails it outright. ``eval``
with a restricted ``__builtins__`` fails it too: every Python object reachable from an
expression carries ``__class__``, and from there ``__subclasses__`` reaches the whole
interpreter. Walking Python's own ``ast`` and refusing the nodes one does not like
fails it in a subtler way — the check is a denylist, and a denylist is only as good as
the author's memory of what Python can do.

So this module does not use Python's parser at all. It has its own tokeniser, its own
parser and its own interpreter over its own node types. The set of things an
expression can denote is the set of node types defined below, and there is no node
type for attribute access, indexing, assignment, import, iteration, or a call to
anything but the seven named functions. An expression cannot reach a Python object
because no node yields one.

The grammar::

    expression := comparison
    comparison := sum (('=' | '<>' | '>' | '>=' | '<' | '<=') sum)?
    sum        := product (('+' | '-') product)*
    product    := unary (('*' | '/') unary)*
    unary      := '-' unary | primary
    primary    := NUMBER | STRING | FIELD | FUNCTION '(' arguments ')' | '(' expression ')'
    FIELD      := '[' NAME ('.' NAME)* ']'
    arguments  := expression (',' expression)*

Numbers are read as ``Decimal`` and never as ``float``, per PRD §9.5: an expression
that computed a total in binary floating point would be a hole in the same hull the
rest of the system is built to keep watertight.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from enum import StrEnum
from typing import Final

from nickland.domain.errors import ValidationError
from nickland.domain.money import ZERO, money

MAXIMUM_LENGTH: Final = 500
"""Characters. A calculated field is a formula, not a program."""

MAXIMUM_DEPTH: Final = 12
"""Nesting levels. Twelve is past anything legible and far short of a stack problem."""

MAXIMUM_NODES: Final = 120
"""Nodes in the parsed tree. Evaluation visits each node once and the tree cannot
contain a cycle, so this bounds the work an expression can cause. There is no loop
construct, so there is nothing else to bound."""

FUNCTIONS: Final[frozenset[str]] = frozenset({"sum", "round", "min", "max", "if", "abs", "words"})
"""The whole function set, per the document designer specification. Nothing further."""

ARITY: Final[dict[str, tuple[int, int]]] = {
    "sum": (1, 1),
    "round": (1, 2),
    "min": (1, 8),
    "max": (1, 8),
    "if": (3, 3),
    "abs": (1, 1),
    "words": (1, 1),
}
"""Minimum and maximum arguments. ``sum`` takes a collection; the rest take values."""


class TokenKind(StrEnum):
    NUMBER = "number"
    STRING = "string"
    FIELD = "field"
    NAME = "name"
    OPERATOR = "operator"
    OPEN = "("
    CLOSE = ")"
    COMMA = ","
    END = "end"


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    text: str
    position: int


COMPARISONS: Final[frozenset[str]] = frozenset({"=", "<>", ">", ">=", "<", "<="})
ARITHMETIC: Final[frozenset[str]] = frozenset({"+", "-", "*", "/"})


def tokenise(source: str) -> list[Token]:
    """Split the source into tokens, refusing any character the grammar has no use for.

    The refusal is an allowlist: a character is either one this grammar reads or it is
    rejected by position. That is the opposite of inspecting Python's parse tree and
    removing what looks dangerous, and it is the reason this module can be reasoned
    about at all.
    """
    if len(source) > MAXIMUM_LENGTH:
        raise ValidationError(
            f"An expression is at most {MAXIMUM_LENGTH} characters; this one is {len(source)}.",
            field="expression",
        )

    tokens: list[Token] = []
    index = 0
    length = len(source)
    while index < length:
        character = source[index]

        if character in " \t\n\r":
            index += 1
            continue

        if character.isdigit() or (character == "." and _peek_digit(source, index + 1)):
            start = index
            seen_point = False
            while index < length and (source[index].isdigit() or source[index] == "."):
                if source[index] == ".":
                    if seen_point:
                        raise ValidationError(
                            f"A number has one decimal point (position {index + 1}).",
                            field="expression",
                        )
                    seen_point = True
                index += 1
            tokens.append(Token(TokenKind.NUMBER, source[start:index], start))
            continue

        if character == '"':
            index += 1
            start = index
            while index < length and source[index] != '"':
                index += 1
            if index >= length:
                raise ValidationError(
                    f"A text value opened at position {start} is never closed.",
                    field="expression",
                )
            tokens.append(Token(TokenKind.STRING, source[start:index], start))
            index += 1
            continue

        if character == "[":
            index += 1
            start = index
            while index < length and source[index] != "]":
                index += 1
            if index >= length:
                raise ValidationError(
                    f"A field reference opened at position {start} is never closed.",
                    field="expression",
                )
            key = source[start:index].strip()
            if not _is_key(key):
                raise ValidationError(
                    f"[{key}] is not a field name. A field is one or more names "
                    "separated by dots, such as [totals.subtotal].",
                    field="expression",
                )
            tokens.append(Token(TokenKind.FIELD, key, start))
            index += 1
            continue

        if character.isalpha() or character == "_":
            start = index
            while index < length and (source[index].isalnum() or source[index] == "_"):
                index += 1
            tokens.append(Token(TokenKind.NAME, source[start:index], start))
            continue

        if character == "(":
            tokens.append(Token(TokenKind.OPEN, character, index))
            index += 1
            continue
        if character == ")":
            tokens.append(Token(TokenKind.CLOSE, character, index))
            index += 1
            continue
        if character == ",":
            tokens.append(Token(TokenKind.COMMA, character, index))
            index += 1
            continue

        two = source[index : index + 2]
        if two in COMPARISONS:
            tokens.append(Token(TokenKind.OPERATOR, two, index))
            index += 2
            continue
        if character in ARITHMETIC or character in {"=", "<", ">"}:
            tokens.append(Token(TokenKind.OPERATOR, character, index))
            index += 1
            continue

        raise ValidationError(
            f"{character!r} at position {index + 1} is not part of the expression "
            "language. The language has numbers, text in double quotes, fields in "
            "square brackets, the four arithmetic operators, comparisons, and the "
            "functions sum, round, min, max, if, abs and words.",
            field="expression",
        )

    tokens.append(Token(TokenKind.END, "", length))
    return tokens


def _peek_digit(source: str, index: int) -> bool:
    return index < len(source) and source[index].isdigit()


def _is_key(key: str) -> bool:
    """Whether a field reference names a field.

    Checked where the reference is read rather than where it is looked up, so that
    ``[x for x in range(10)]`` is refused as malformed rather than accepted as the
    name of a field that happens not to exist. The two are refused either way; only
    one of them tells the author what is actually wrong.
    """
    if not key:
        return False
    return all(
        part
        and (part[0].isalpha() or part[0] == "_")
        and all(character.isalnum() or character == "_" for character in part)
        for part in key.split(".")
    )


# --------------------------------------------------------------------------------------
# The node types. This list is the whole of what an expression can denote.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Literal:
    """A number or a piece of text written in the expression."""

    value: Decimal | str


@dataclass(frozen=True, slots=True)
class FieldReference:
    """A value drawn from the palette, by key."""

    key: str


@dataclass(frozen=True, slots=True)
class Unary:
    operator: str
    operand: Node


@dataclass(frozen=True, slots=True)
class Binary:
    operator: str
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    arguments: tuple[Node, ...]


Node = Literal | FieldReference | Unary | Binary | Call
"""Every node type there is. There is no node for attribute access, for indexing, for
assignment, for a loop, for an import, or for a call to anything outside ``FUNCTIONS``,
so an expression has no way to denote any of those things."""


@dataclass(frozen=True, slots=True)
class Expression:
    """A parsed expression, with the source it came from and what it reads."""

    source: str
    root: Node
    references: frozenset[str]
    nodes: int
    depth: int


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Parser:
    tokens: list[Token]
    position: int = 0
    nodes: int = 0
    references: set[str] = field(default_factory=set)

    @property
    def current(self) -> Token:
        return self.tokens[self.position]

    def at(self, kind: TokenKind) -> bool:
        """Whether the next token is of a kind.

        A method rather than a comparison on ``self.current.kind``, because a type
        checker treats the property read as pure and narrows across the ``advance``
        that follows it — concluding that a token which had to be an opening bracket
        is still one after the parser has moved past it.
        """
        return self.tokens[self.position].kind is kind

    def at_operator(self, *options: str) -> bool:
        token = self.tokens[self.position]
        return token.kind is TokenKind.OPERATOR and token.text in options

    def advance(self) -> Token:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def count(self) -> None:
        self.nodes += 1
        if self.nodes > MAXIMUM_NODES:
            raise ValidationError(
                f"An expression holds at most {MAXIMUM_NODES} terms. Break the "
                "calculation into two calculated fields.",
                field="expression",
            )

    def parse(self) -> Node:
        node = self.expression(depth=1)
        if not self.at(TokenKind.END):
            raise ValidationError(
                f"Unexpected {self.current.text!r} at position {self.current.position + 1}.",
                field="expression",
            )
        return node

    def expression(self, *, depth: int) -> Node:
        if depth > MAXIMUM_DEPTH:
            raise ValidationError(
                f"An expression nests at most {MAXIMUM_DEPTH} deep.", field="expression"
            )
        left = self.sum(depth=depth)
        if self.at_operator(*COMPARISONS):
            operator = self.advance().text
            right = self.sum(depth=depth)
            self.count()
            return Binary(operator, left, right)
        return left

    def sum(self, *, depth: int) -> Node:
        node = self.product(depth=depth)
        while self.at_operator("+", "-"):
            operator = self.advance().text
            right = self.product(depth=depth)
            self.count()
            node = Binary(operator, node, right)
        return node

    def product(self, *, depth: int) -> Node:
        node = self.unary(depth=depth)
        while self.at_operator("*", "/"):
            operator = self.advance().text
            right = self.unary(depth=depth)
            self.count()
            node = Binary(operator, node, right)
        return node

    def unary(self, *, depth: int) -> Node:
        if self.at_operator("-"):
            self.advance()
            self.count()
            return Unary("-", self.unary(depth=depth))
        return self.primary(depth=depth)

    def primary(self, *, depth: int) -> Node:
        token = self.advance()
        self.count()

        if token.kind is TokenKind.NUMBER:
            try:
                return Literal(money(token.text))
            except (ValueError, InvalidOperation) as error:
                raise ValidationError(
                    f"{token.text!r} is not a number.", field="expression"
                ) from error

        if token.kind is TokenKind.STRING:
            return Literal(token.text)

        if token.kind is TokenKind.FIELD:
            if not token.text:
                raise ValidationError("An empty field reference.", field="expression")
            self.references.add(token.text)
            return FieldReference(token.text)

        if token.kind is TokenKind.NAME:
            name = token.text.lower()
            if name not in FUNCTIONS:
                raise ValidationError(
                    f"{token.text!r} is not one of the available functions. The "
                    f"language offers {', '.join(sorted(FUNCTIONS))} and nothing else. "
                    "A field goes in square brackets.",
                    field="expression",
                )
            if not self.at(TokenKind.OPEN):
                raise ValidationError(
                    f"{name} is a function and needs its arguments in brackets.",
                    field="expression",
                )
            self.advance()
            arguments: list[Node] = []
            if not self.at(TokenKind.CLOSE):
                arguments.append(self.expression(depth=depth + 1))
                while self.at(TokenKind.COMMA):
                    self.advance()
                    arguments.append(self.expression(depth=depth + 1))
            if not self.at(TokenKind.CLOSE):
                raise ValidationError(f"{name} is missing its closing bracket.", field="expression")
            self.advance()
            least, most = ARITY[name]
            if not least <= len(arguments) <= most:
                expected = f"{least}" if least == most else f"between {least} and {most}"
                raise ValidationError(
                    f"{name} takes {expected} argument(s); {len(arguments)} were given.",
                    field="expression",
                )
            return Call(name, tuple(arguments))

        if token.kind is TokenKind.OPEN:
            node = self.expression(depth=depth + 1)
            if not self.at(TokenKind.CLOSE):
                raise ValidationError("A bracket is never closed.", field="expression")
            self.advance()
            return node

        raise ValidationError(
            f"Unexpected {token.text or 'end of expression'!r} at position {token.position + 1}.",
            field="expression",
        )


def parse(source: str) -> Expression:
    """Parse an expression, or say why it is not one."""
    if not source.strip():
        raise ValidationError("A calculated field needs an expression.", field="expression")
    parser = _Parser(tokenise(source))
    root = parser.parse()
    return Expression(
        source=source,
        root=root,
        references=frozenset(parser.references),
        nodes=parser.nodes,
        depth=_depth(root),
    )


def _depth(node: Node) -> int:
    if isinstance(node, Unary):
        return 1 + _depth(node.operand)
    if isinstance(node, Binary):
        return 1 + max(_depth(node.left), _depth(node.right))
    if isinstance(node, Call):
        return 1 + max((_depth(item) for item in node.arguments), default=0)
    return 1


# --------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------

Value = Decimal | str | bool | date | None


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """What an expression may read. Nothing else is reachable from it."""

    values: Mapping[str, Value] = field(default_factory=dict)
    collections: Mapping[str, Sequence[Decimal]] = field(default_factory=dict)
    currency_unit: str = "cedis"
    currency_subunit: str = "pesewas"


class ExpressionError(ValidationError):
    """An expression could not be evaluated.

    Raised rather than returned, and caught by the renderer, which substitutes a
    stated placeholder and records a diagnostic. A document is never abandoned
    because one calculated field could not be worked out, per the specification.
    """

    code = "expression_failed"


def evaluate(expression: Expression, context: EvaluationContext) -> Value:
    """Walk the tree. The interpreter has no access to anything but ``context``."""
    with localcontext() as arithmetic:
        arithmetic.prec = 34
        return _evaluate(expression.root, context)


def _evaluate(node: Node, context: EvaluationContext) -> Value:
    if isinstance(node, Literal):
        return node.value

    if isinstance(node, FieldReference):
        if node.key in context.values:
            return context.values[node.key]
        if node.key in context.collections:
            raise ExpressionError(
                f"[{node.key}] is a column of values. Wrap it in sum().",
                field="expression",
            )
        raise ExpressionError(f"[{node.key}] is not a field on this document.", field="expression")

    if isinstance(node, Unary):
        return _negate(_evaluate(node.operand, context))

    if isinstance(node, Binary):
        return _binary(node.operator, node.left, node.right, context)

    return _call(node, context)


def _binary(operator: str, left: Node, right: Node, context: EvaluationContext) -> Value:
    first = _evaluate(left, context)
    second = _evaluate(right, context)

    if operator in COMPARISONS:
        return _compare(operator, first, second)

    if operator == "+" and (isinstance(first, str) or isinstance(second, str)):
        return f"{_text(first)}{_text(second)}"

    a = _number(first, operator)
    b = _number(second, operator)
    if operator == "+":
        return money(a + b)
    if operator == "-":
        return money(a - b)
    if operator == "*":
        return money(a * b)
    if b == ZERO:
        raise ExpressionError("Division by zero.", field="expression")
    try:
        return money(a / b)
    except (DivisionByZero, InvalidOperation) as error:  # pragma: no cover - guarded above
        raise ExpressionError("Division by zero.", field="expression") from error


def _compare(operator: str, first: Value, second: Value) -> bool:
    if operator == "=":
        return _equal(first, second)
    if operator == "<>":
        return not _equal(first, second)
    if isinstance(first, str) or isinstance(second, str):
        left_text, right_text = _text(first), _text(second)
        return _ordered(operator, left_text, right_text)
    return _ordered(operator, _number(first, operator), _number(second, operator))


def _ordered(operator: str, first: Decimal | str, second: Decimal | str) -> bool:
    if operator == ">":
        return first > second  # type: ignore[operator]
    if operator == ">=":
        return first >= second  # type: ignore[operator]
    if operator == "<":
        return first < second  # type: ignore[operator]
    return first <= second  # type: ignore[operator]


def _equal(first: Value, second: Value) -> bool:
    """Equality as a document author means it.

    Two numbers compare as numbers. Everything else compares as text, which makes an
    absent value equal to an empty one: on a page, a manufacturer that was never
    recorded and a manufacturer recorded as blank are the same absence, and
    ``if([product.manufacturer] = "", "Not stated", ...)`` is how an author says so.

    An absent value is still not equal to a number, because the distinction between
    "no figure" and "zero" is one the rest of the system takes care to keep.
    """
    if isinstance(first, Decimal) and isinstance(second, Decimal):
        return first == second
    if isinstance(first, Decimal) or isinstance(second, Decimal):
        return False if first is None or second is None else _text(first) == _text(second)
    return _text(first) == _text(second)


def _call(node: Call, context: EvaluationContext) -> Value:
    name = node.name

    if name == "sum":
        target = node.arguments[0]
        if not isinstance(target, FieldReference):
            raise ExpressionError(
                "sum() adds up one column, so its argument is a field reference such "
                "as sum([line.total]).",
                field="expression",
            )
        if target.key not in context.collections:
            raise ExpressionError(
                f"[{target.key}] is not a column that can be added up.",
                field="expression",
            )
        return money(sum(context.collections[target.key], ZERO))

    if name == "if":
        condition = _evaluate(node.arguments[0], context)
        chosen = node.arguments[1] if _truthy(condition) else node.arguments[2]
        return _evaluate(chosen, context)

    values = [_evaluate(argument, context) for argument in node.arguments]

    if name == "abs":
        return abs(_number(values[0], name))
    if name == "min":
        return min(_number(value, name) for value in values)
    if name == "max":
        return max(_number(value, name) for value in values)
    if name == "round":
        places = int(_number(values[1], name)) if len(values) == 2 else 2
        if not 0 <= places <= 6:
            raise ExpressionError("round() works to between 0 and 6 places.", field="expression")
        from nickland.domain.money import present

        return present(_number(values[0], name), places)

    from nickland.domain.documents import amount_in_words

    return amount_in_words(
        _number(values[0], name),
        unit=context.currency_unit,
        subunit=context.currency_subunit,
    )


def _truthy(value: Value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, Decimal):
        return value != ZERO
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _negate(value: Value) -> Decimal:
    return -_number(value, "-")


def _number(value: Value, context_name: str) -> Decimal:
    if isinstance(value, bool):
        return Decimal("1") if value else ZERO
    if isinstance(value, Decimal):
        return value
    if value is None:
        return ZERO
    if isinstance(value, str):
        try:
            return money(value.strip() or "0")
        except (ValueError, InvalidOperation) as error:
            raise ExpressionError(
                f"{value!r} is text, and {context_name} needs a number.",
                field="expression",
            ) from error
    raise ExpressionError(f"A date cannot be used with {context_name}.", field="expression")


def _text(value: Value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def render_value(
    expression: Expression, context: EvaluationContext, *, placeholder: str = "—"
) -> str:
    """Evaluate for display, substituting a placeholder rather than failing the document.

    The specification is explicit that a failed expression renders a placeholder and
    records a diagnostic, and never aborts the document. A pro forma missing one
    computed line is a document somebody can still read and correct; a pro forma that
    did not render is a sale that did not go out.
    """
    try:
        return _text(evaluate(expression, context))
    except ValidationError:
        return placeholder


def diagnose(expression: Expression, context: EvaluationContext) -> str | None:
    """Why an expression failed, or ``None`` if it did not."""
    try:
        evaluate(expression, context)
    except ValidationError as error:
        return error.message
    return None
