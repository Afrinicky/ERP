"""The calculated-field language.

PRD §6.6 requires "safe constrained expressions, not arbitrary code execution", and
the Phase 6 exit criteria require that to be proven rather than asserted. The proof
is the corpus below: every construct by which an expression language is ordinarily
escaped, each refused, most of them before evaluation begins.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nickland.domain.errors import ValidationError
from nickland.domain.expressions import (
    FUNCTIONS,
    MAXIMUM_DEPTH,
    MAXIMUM_LENGTH,
    MAXIMUM_NODES,
    Binary,
    Call,
    EvaluationContext,
    FieldReference,
    Literal,
    Unary,
    diagnose,
    evaluate,
    parse,
    render_value,
    tokenise,
)

D = Decimal

CONTEXT = EvaluationContext(
    values={
        "totals.subtotal": D("1200.00"),
        "totals.tax": D("180.00"),
        "totals.grand_total": D("1380.00"),
        "customer.name": "Banhart Hospital",
        "transaction.issue_date": date(2026, 11, 6),
        "line.manufacturer": None,
        "payment.plan": "instalment",
    },
    collections={"line.total": [D("300"), D("500"), D("400")], "line.quantity": [D("2"), D("3")]},
)


# --------------------------------------------------------------------------------------
# The exit criterion: no expression can execute arbitrary code
# --------------------------------------------------------------------------------------


ESCAPES = [
    pytest.param("__import__('os').system('id')", id="import-call"),
    pytest.param("().__class__", id="class-of-tuple"),
    pytest.param("[].__class__.__base__.__subclasses__()", id="subclasses"),
    pytest.param("''.__class__.__mro__", id="mro"),
    pytest.param("(1).__class__", id="class-of-int"),
    pytest.param("[x for x in range(10)]", id="comprehension"),
    pytest.param("lambda: 1", id="lambda"),
    pytest.param("exec('print(1)')", id="exec"),
    pytest.param("eval('1')", id="eval"),
    pytest.param("open('/etc/passwd')", id="open"),
    pytest.param("globals()", id="globals"),
    pytest.param("locals()", id="locals"),
    pytest.param("dir()", id="dir"),
    pytest.param("getattr(1, 'real')", id="getattr"),
    pytest.param("vars()", id="vars"),
    pytest.param("compile('1', '', 'eval')", id="compile"),
    pytest.param("a := 1", id="walrus"),
    pytest.param("import os", id="import-statement"),
    pytest.param("print(1)", id="print"),
    pytest.param("self", id="self"),
    pytest.param("context", id="context"),
    pytest.param("[a].b", id="attribute-on-field"),
    pytest.param("[a][0]", id="index-on-field"),
    pytest.param("sum[1]", id="index-on-function"),
    pytest.param("round.__call__(1)", id="dunder-call"),
    pytest.param("1;2", id="statement-separator"),
    pytest.param("`id`", id="backtick"),
    pytest.param("$(id)", id="shell"),
    pytest.param("{1:2}", id="dict"),
    pytest.param("1 & 2", id="bitwise-and"),
    pytest.param("1 | 2", id="bitwise-or"),
    pytest.param("1 ** 2", id="power"),
    pytest.param("1 // 2", id="floor-divide"),
    pytest.param("1 % 2", id="modulo"),
    pytest.param("~1", id="invert"),
    pytest.param("not 1", id="not"),
    pytest.param("1 and 2", id="and"),
    pytest.param("'single quoted'", id="single-quote"),
    pytest.param("\\x41", id="escape"),
    pytest.param("[os.environ]", id="field-named-like-a-module"),
]


@pytest.mark.parametrize("source", ESCAPES)
def test_no_expression_reaches_outside_the_language(source: str) -> None:
    """Each of these is refused, and none of them evaluates to anything.

    Refusal at parse time is the usual outcome and the preferred one. A few parse as
    a field reference to a field that does not exist, and are refused at evaluation.
    Either way nothing outside ``CONTEXT`` is reachable, because no node type in the
    language denotes anything else.
    """
    try:
        expression = parse(source)
    except ValidationError:
        return
    with pytest.raises(ValidationError):
        evaluate(expression, CONTEXT)


def test_the_node_types_are_the_whole_language() -> None:
    """There is no node for attribute access, indexing, assignment or a loop.

    This is the structural half of the argument: the corpus above shows that known
    escapes are refused, and this shows there is nothing for an unknown one to use.
    """
    from typing import get_args

    from nickland.domain.expressions import Node

    assert {item.__name__ for item in get_args(Node)} == {
        "Literal",
        "FieldReference",
        "Unary",
        "Binary",
        "Call",
    }


def test_only_the_named_functions_exist() -> None:
    assert {"sum", "round", "min", "max", "if", "abs", "words"} == FUNCTIONS
    for name in ("print", "open", "eval", "exec", "getattr", "input", "len", "type"):
        with pytest.raises(ValidationError, match="not one of the available functions"):
            parse(f"{name}(1)")


# --------------------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------------------


class TestBounds:
    def test_length(self) -> None:
        with pytest.raises(ValidationError, match="at most 500 characters"):
            parse("1" + "+1" * MAXIMUM_LENGTH)

    def test_depth(self) -> None:
        with pytest.raises(ValidationError, match=f"nests at most {MAXIMUM_DEPTH}"):
            parse("(" * (MAXIMUM_DEPTH + 2) + "1" + ")" * (MAXIMUM_DEPTH + 2))

    def test_node_count(self) -> None:
        with pytest.raises(ValidationError, match=f"at most {MAXIMUM_NODES} terms"):
            parse("1" + "+1" * MAXIMUM_NODES)

    def test_a_formula_within_the_bounds_parses(self) -> None:
        expression = parse("round((sum([line.total]) + [totals.tax]) * 2, 2)")
        assert expression.nodes < MAXIMUM_NODES
        assert expression.depth <= MAXIMUM_DEPTH


# --------------------------------------------------------------------------------------
# What the language is for
# --------------------------------------------------------------------------------------


class TestArithmetic:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("1 + 2", "3"),
            ("10 - 4", "6"),
            ("6 * 7", "42"),
            ("10 / 4", "2.5"),
            ("-5", "-5"),
            ("2 + 3 * 4", "14"),
            ("(2 + 3) * 4", "20"),
            ("[totals.subtotal] + [totals.tax]", "1380"),
            ("[totals.subtotal] * 0.15", "180"),
        ],
    )
    def test_evaluates(self, source: str, expected: str) -> None:
        assert evaluate(parse(source), CONTEXT) == D(expected)

    def test_division_by_zero_is_refused_rather_than_raised_as_arithmetic(self) -> None:
        with pytest.raises(ValidationError, match="Division by zero"):
            evaluate(parse("1 / 0"), CONTEXT)

    def test_a_number_is_a_decimal_never_a_float(self) -> None:
        value = evaluate(parse("0.1 + 0.2"), CONTEXT)
        assert isinstance(value, Decimal)
        assert value == D("0.3")


class TestFunctions:
    def test_sum_adds_a_column(self) -> None:
        assert evaluate(parse("sum([line.total])"), CONTEXT) == D("1200")

    def test_sum_needs_a_column_not_a_value(self) -> None:
        with pytest.raises(ValidationError, match="adds up one column"):
            evaluate(parse("sum(1 + 2)"), CONTEXT)

    def test_sum_of_a_scalar_field_says_so(self) -> None:
        with pytest.raises(ValidationError, match="not a column"):
            evaluate(parse("sum([totals.tax])"), CONTEXT)

    def test_a_column_used_without_sum_says_so(self) -> None:
        with pytest.raises(ValidationError, match="Wrap it in sum"):
            evaluate(parse("[line.total] + 1"), CONTEXT)

    def test_round(self) -> None:
        assert evaluate(parse("round(1.23456, 2)"), CONTEXT) == D("1.23")
        assert evaluate(parse("round(1.23456)"), CONTEXT) == D("1.23")

    def test_round_beyond_six_places_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="between 0 and 6"):
            evaluate(parse("round(1.5, 9)"), CONTEXT)

    def test_min_max_and_abs(self) -> None:
        assert evaluate(parse("min(3, 1, 2)"), CONTEXT) == D("1")
        assert evaluate(parse("max(3, 1, 2)"), CONTEXT) == D("3")
        assert evaluate(parse("abs(0 - 7)"), CONTEXT) == D("7")

    def test_if_chooses(self) -> None:
        assert evaluate(parse('if(1 > 0, "yes", "no")'), CONTEXT) == "yes"
        assert evaluate(parse('if(1 < 0, "yes", "no")'), CONTEXT) == "no"

    def test_words_spells_an_amount(self) -> None:
        spelled = evaluate(parse("words(1200)"), CONTEXT)
        assert isinstance(spelled, str)
        assert "thousand" in spelled

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ("round()", "takes between 1 and 2"),
            ("if(1, 2)", "takes 3 argument"),
            ("abs(1, 2)", "takes 1 argument"),
            ("sum()", "takes 1 argument"),
        ],
    )
    def test_arity_is_checked(self, source: str, message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            parse(source)


class TestComparison:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("1 = 1", True),
            ("1 <> 2", True),
            ("2 > 1", True),
            ("1 >= 1", True),
            ("1 < 2", True),
            ("2 <= 1", False),
            ('[payment.plan] = "instalment"', True),
            ('[customer.name] <> ""', True),
        ],
    )
    def test_compares(self, source: str, expected: bool) -> None:
        assert evaluate(parse(source), CONTEXT) is expected

    def test_an_absent_value_reads_as_empty(self) -> None:
        """On a page, a manufacturer never recorded and one recorded blank are one
        absence, and an author writes ``= ""`` for both."""
        assert evaluate(parse('[line.manufacturer] = ""'), CONTEXT) is True

    def test_an_absent_value_is_not_zero(self) -> None:
        """The distinction between no figure and zero is one the system keeps."""
        assert evaluate(parse("[line.manufacturer] = 0"), CONTEXT) is False


class TestText:
    def test_concatenation(self) -> None:
        assert evaluate(parse('"Invoice for " + [customer.name]'), CONTEXT) == (
            "Invoice for Banhart Hospital"
        )

    def test_a_date_reads_as_its_iso_form(self) -> None:
        assert evaluate(parse("[transaction.issue_date]"), CONTEXT) == date(2026, 11, 6)
        assert render_value(parse("[transaction.issue_date]"), CONTEXT) == "2026-11-06"


class TestFieldReferences:
    def test_a_malformed_reference_is_refused_where_it_is_read(self) -> None:
        with pytest.raises(ValidationError, match="is not a field name"):
            parse("[not a field name!]")

    def test_an_unknown_field_is_refused_at_evaluation(self) -> None:
        with pytest.raises(ValidationError, match="not a field on this document"):
            evaluate(parse("[customer.favourite_colour]"), CONTEXT)

    def test_the_references_are_reported(self) -> None:
        expression = parse("[totals.subtotal] + [totals.tax]")
        assert expression.references == {"totals.subtotal", "totals.tax"}

    def test_an_empty_reference_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not a field name"):
            parse("[]")


class TestFailureHandling:
    def test_a_failed_expression_renders_a_placeholder(self) -> None:
        """The specification: a failure never aborts the document."""
        assert render_value(parse("1 / 0"), CONTEXT) == "—"
        assert render_value(parse("1 / 0"), CONTEXT, placeholder="n/a") == "n/a"

    def test_the_diagnostic_says_why(self) -> None:
        assert diagnose(parse("1 / 0"), CONTEXT) == "Division by zero."
        assert diagnose(parse("1 + 1"), CONTEXT) is None

    def test_an_empty_expression_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="needs an expression"):
            parse("   ")


class TestTokeniser:
    def test_an_unterminated_text_value(self) -> None:
        with pytest.raises(ValidationError, match=r"never closed"):
            tokenise('"open')

    def test_an_unterminated_field_reference(self) -> None:
        with pytest.raises(ValidationError, match=r"never closed"):
            tokenise("[open")

    def test_two_decimal_points(self) -> None:
        with pytest.raises(ValidationError, match="one decimal point"):
            tokenise("1.2.3")

    def test_an_unknown_character_names_its_position(self) -> None:
        with pytest.raises(ValidationError, match="position 3"):
            tokenise("1 @ 2")


class TestParsing:
    def test_the_tree_is_built_from_the_language_s_own_nodes(self) -> None:
        root = parse("round([totals.tax] * -2, 2)").root
        assert isinstance(root, Call)
        assert root.name == "round"
        product = root.arguments[0]
        assert isinstance(product, Binary)
        assert isinstance(product.left, FieldReference)
        assert isinstance(product.right, Unary)
        assert isinstance(product.right.operand, Literal)

    def test_an_unclosed_bracket(self) -> None:
        with pytest.raises(ValidationError, match=r"never closed|missing its closing"):
            parse("(1 + 2")

    def test_a_function_without_brackets(self) -> None:
        with pytest.raises(ValidationError, match="needs its arguments in brackets"):
            parse("abs 1")

    def test_trailing_rubbish(self) -> None:
        with pytest.raises(ValidationError, match="Unexpected"):
            parse("1 2")
