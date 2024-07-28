from __future__ import annotations

from typing import Any

import narwhals as nw


def test_null_count(constructor_eager: Any) -> None:
    data = [1, 2, None]
    series = nw.from_native(constructor_eager({"a": data}), eager_only=True)["a"]
    result = series.null_count()
    assert result == 1
