from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sark.dpkg import read_pkg, to_df, _schema, _source_type


class noop_map:
    def __getitem__(self, key):
        return key


def test_source_type_heuristics():
    no_file = "/path/to/non-existent-file.ext"
    with pytest.raises(ValueError):
        _source_type(no_file)


@pytest.mark.skip(reason="not sure how to test schema parsing")
def test_schema_parsing():
    pass


def test_pkg_creation(pkg):
    resource, *_ = pkg.resources
    expected = {
        # datapackage.Package.infer(..) infers datetime as string
        "time": "string",
        "QWE": "integer",
        "RTY": "boolean",
        "UIO": "string",
        "ASD": "number",
        "FGH": "number",
        "JKL": "number",
        "ZXC": "number",
        "VBN": "number",
        "MPQ": "string",
    }
    assert _schema(resource, noop_map()) == expected


def test_pkg_read(pkgdir):
    dpkg_json = pkgdir / "datapackage.json"
    pkg = read_pkg(dpkg_json)
    assert all(Path(res.source).exists() for res in pkg.resources)


def test_pkg_conversion(pkgdir):
    dpkg_json = pkgdir / "datapackage.json"
    pkg = read_pkg(dpkg_json)
    resource, *_ = pkg.resources
    df = to_df(resource)
    expected = {
        "time": np.dtype("datetime64[ns]"),
        "QWE": np.dtype("int"),
        "RTY": np.dtype("bool"),
        "UIO": pd.StringDtype(),
        "ASD": np.dtype("float"),
        "FGH": np.dtype("float"),
        "JKL": np.dtype("float"),
        "ZXC": np.dtype("float"),
        "VBN": np.dtype("float"),
        "MPQ": pd.StringDtype(),
    }

    assert df.dtypes.to_dict() == expected
