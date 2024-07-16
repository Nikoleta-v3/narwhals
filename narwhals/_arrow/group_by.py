from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable

from narwhals._expression_parsing import is_simple_aggregation
from narwhals._expression_parsing import parse_into_exprs
from narwhals.dependencies import get_pyarrow
from narwhals.dependencies import get_pyarrow_compute
from narwhals.utils import remove_prefix

if TYPE_CHECKING:
    from narwhals._arrow.dataframe import ArrowDataFrame
    from narwhals._arrow.expr import ArrowExpr
    from narwhals._arrow.typing import IntoArrowExpr

POLARS_TO_ARROW_AGGREGATIONS = {
    "len": "size",
}


class ArrowGroupBy:
    def __init__(self, df: ArrowDataFrame, keys: list[str]) -> None:
        pa = get_pyarrow()
        self._df = df
        self._keys = list(keys)
        self._grouped = pa.TableGroupBy(self._df._native_dataframe, list(self._keys))

    def agg(
        self,
        *aggs: IntoArrowExpr,
        **named_aggs: IntoArrowExpr,
    ) -> ArrowDataFrame:
        exprs = parse_into_exprs(
            *aggs,
            namespace=self._df.__narwhals_namespace__(),
            **named_aggs,
        )
        output_names: list[str] = copy(self._keys)
        for expr in exprs:
            if expr._output_names is None:
                msg = (
                    "Anonymous expressions are not supported in group_by.agg.\n"
                    "Instead of `nw.all()`, try using a named expression, such as "
                    "`nw.col('a', 'b')`\n"
                )
                raise ValueError(msg)
            output_names.extend(expr._output_names)

        return agg_arrow(
            self._grouped,
            exprs,
            self._keys,
            output_names,
            self._df._from_native_dataframe,
        )


def agg_arrow(
    grouped: Any,
    exprs: list[ArrowExpr],
    keys: list[str],
    output_names: list[str],
    from_dataframe: Callable[[Any], ArrowDataFrame],
) -> ArrowDataFrame:
    pc = get_pyarrow_compute()
    all_simple_aggs = True
    for expr in exprs:
        if not is_simple_aggregation(expr):
            all_simple_aggs = False
            break

    if all_simple_aggs:
        # Mapping from output name to
        # (input_column_name, function_name, pyarrow_output_name)  # noqa: ERA001
        simple_aggregations: dict[str, tuple[Any, str, str]] = {}
        for expr in exprs:
            if expr._depth == 0:
                # e.g. agg(nw.len()) # noqa: ERA001
                if (
                    expr._output_names is None or expr._function_name != "len"
                ):  # pragma: no cover
                    msg = "Safety assertion failed, please report a bug to https://github.com/narwhals-dev/narwhals/issues"
                    raise AssertionError(msg)
                simple_aggregations[expr._output_names[0]] = (
                    keys[0],
                    "count",
                    f"{keys[0]}_count",
                )
                continue

            # e.g. agg(nw.mean('a')) # noqa: ERA001
            if (
                expr._depth != 1 or expr._root_names is None or expr._output_names is None
            ):  # pragma: no cover
                msg = "Safety assertion failed, please report a bug to https://github.com/narwhals-dev/narwhals/issues"
                raise AssertionError(msg)

            function_name = remove_prefix(expr._function_name, "col->")
            function_name = POLARS_TO_ARROW_AGGREGATIONS.get(function_name, function_name)
            for root_name, output_name in zip(expr._root_names, expr._output_names):
                simple_aggregations[output_name] = (
                    root_name,
                    function_name,
                    f"{root_name}_{function_name}",
                )

        aggs: list[Any] = []
        name_mapping = {}
        for output_name, named_agg in simple_aggregations.items():
            if named_agg[1] == "count":
                aggs.append((named_agg[0], named_agg[1], pc.CountOptions(mode="all")))
            else:
                aggs.append((named_agg[0], named_agg[1]))
            name_mapping[named_agg[2]] = output_name
        result_simple = grouped.aggregate(aggs)
        result_simple = result_simple.rename_columns(
            [name_mapping.get(col, col) for col in result_simple.column_names]
        ).select(output_names)
        return from_dataframe(result_simple)

    msg = (
        "Non-trivial complex found.\n\n"
        "Hint: you were probably trying to apply a non-elementary aggregation with a "
        "pyarrow table.\n"
        "Please rewrite your query such that group-by aggregations "
        "are elementary. For example, instead of:\n\n"
        "    df.group_by('a').agg(nw.col('b').round(2).mean())\n\n"
        "use:\n\n"
        "    df.with_columns(nw.col('b').round(2)).group_by('a').agg(nw.col('b').mean())\n\n"
    )
    raise ValueError(msg)
