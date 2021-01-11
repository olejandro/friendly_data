from contextlib import nullcontext as does_not_raise
import json
from operator import contains
from pathlib import Path

from glom import Assign, glom, Iter, T
import numpy as np
import pandas as pd
import pytest

from sark.dpkg import (
    create_pkg,
    index_levels,
    pkg_from_index,
    pkg_glossary,
    read_pkg,
    registry,
    to_df,
    update_pkg,
    write_pkg,
    read_pkg_index,
    _schema,
    _source_type,
)
from sark.helpers import match, select
from sark.metatools import get_license


class noop_map:
    def __getitem__(self, key):
        return key


# values as per noop
default_type_map = {
    "object": "string",
    "float64": "number",
    "int64": "integer",
    "Int64": "integer",
    "bool": "boolean",
}


def expected_schema(df, type_map=default_type_map):
    # handle a resource and a path
    if not isinstance(df, pd.DataFrame):
        try:
            df = pd.read_csv(df.source)
        except AttributeError:
            df = pd.read_csv(df)  # path
    # datapackage.Package.infer(..) relies on tableschema.Schema.infer(..),
    # which infers datetime as string
    return (
        df.dtypes.astype(str)
        .map(lambda t: type_map[t] if t in type_map else t)
        .to_dict()
    )


def test_source_type_heuristics():
    no_file = "/path/to/non-existent-file.ext"
    with pytest.raises(ValueError):
        _source_type(no_file)


@pytest.mark.skip(reason="not sure how to test schema parsing")
def test_schema_parsing():
    pass


def test_pkg_creation(pkgdir, subtests):
    pkg_meta = {"name": "test", "licenses": get_license("CC0-1.0")}
    csvs = [f.relative_to(pkgdir) for f in (pkgdir / "data").glob("sample-ok-?.csv")]
    pkg = create_pkg(pkg_meta, csvs, pkgdir)
    for resource in pkg.resources:
        with subtests.test(msg="resource", name=resource.name):
            assert _schema(resource, noop_map()) == expected_schema(resource)


def test_pkg_read(pkgdir):
    dpkg_json = pkgdir / "datapackage.json"
    pkg = read_pkg(dpkg_json)
    assert all(Path(res.source).exists() for res in pkg.resources)


def test_zippkg_read(pkg, tmp_path_factory, subtests):
    with tmp_path_factory.mktemp("ziptest-") as tmpdir:
        zipfile = tmpdir / "testpackage.zip"
        pkg.save(f"{zipfile}")

        testname = f"{zipfile} -> {zipfile.parent}"
        with subtests.test(msg="extract zip in current dir", name=testname):
            pkg = read_pkg(zipfile)
            assert pkg.valid

        subdir = zipfile.parent / "foo"
        testname = f"{zipfile} -> {subdir}"
        with subtests.test(msg="extract zip in different dir", name=testname):
            pkg2 = read_pkg(zipfile, extract_dir=subdir)
            assert pkg2.valid

        with subtests.test(msg="unsupported archive", name="tarball"):
            tarball = tmpdir / "testpackage.tar"
            with pytest.raises(ValueError, match=f".*{tarball.name}:.+"):
                read_pkg(tarball)


def test_pkg_update(pkg, subtests):
    with subtests.test(msg="schema field update", name="single"):
        resource_name = "sample-ok-1"
        update = {"time": {"name": "time", "type": "string", "format": "default"}}
        assert update_pkg(pkg, resource_name, update)
        res, *_ = glom(
            pkg.descriptor,
            (
                "resources",
                [select(T["name"], equal_to=resource_name)],
                "0.schema.fields",
                [select(T["name"], equal_to="time")],
            ),
        )
        assert update["time"] == res

    with subtests.test(msg="schema field update", name="multiple"):
        update = {
            "time": {"name": "time", "type": "datetime", "format": "default"},
            "QWE": {"name": "QWE", "type": "string", "format": "default"},
        }
        assert update_pkg(pkg, resource_name, update)
        res = glom(
            pkg.descriptor,
            (
                "resources",
                [select(T["name"], equal_to=resource_name)],
                "0.schema.fields",
                [select(T["name"], one_of=update.keys())],
            ),
        )
        assert list(update.values()) == res

    with subtests.test(msg="schema NA/index update"):
        resource_name = "sample-ok-2"
        update = {"primaryKey": ["lvl", "TRE", "IUY"]}
        assert update_pkg(pkg, resource_name, update, fields=False)
        res = glom(
            pkg.descriptor,
            (
                "resources",
                [select(T["name"], equal_to=resource_name)],
                "0.schema.primaryKey",
            ),
        )
        assert update["primaryKey"] == res

    # FIXME: test assertions inside update_pkg


def test_read_pkg_index(pkg_index):
    idx = read_pkg_index(*pkg_index)  # contents, suffix
    np.testing.assert_array_equal(idx.columns, ["file", "name", "idxcols"])
    assert idx.shape == (3, 3)
    np.testing.assert_array_equal(idx["idxcols"].agg(len), [2, 3, 1])


def test_read_pkg_index_errors(tmp_path):
    idxfile = tmp_path / "index.txt"
    with pytest.raises(FileNotFoundError):
        read_pkg_index(idxfile)

    idxfile.touch()
    with pytest.raises(RuntimeError, match=".+index.txt: unknown index.+"):
        read_pkg_index(idxfile)

    idxfile = idxfile.with_suffix(".json")
    bad_data = {"file": "file1", "name": "dst1", "idxcols": ["cola", "colb"]}
    idxfile.write_text(json.dumps(bad_data))
    with pytest.raises(RuntimeError, match=".+index.json: bad index file"):
        read_pkg_index(idxfile)


@pytest.mark.parametrize(
    "col, col_t, expectation",
    [
        ("locs", "idxcols", does_not_raise()),
        ("storage", "cols", does_not_raise()),
        (
            "notinreg",
            "cols",
            pytest.warns(RuntimeWarning, match=f"notinreg: not in registry"),
        ),
        (
            "timesteps",
            "bad_col_t",
            pytest.raises(ValueError, match=f"bad_col_t: unknown column type"),
        ),
    ],
)
def test_registry(col, col_t, expectation):
    with expectation:
        res = registry(col, col_t)
        assert isinstance(res, dict)
        if col == "notinreg":
            assert res == {}


@pytest.mark.parametrize(
    "csvfile, idxcols, ncatcols",
    [
        ("inputs/cost_energy_cap.csv", ["costs", "locs", "techs"], 3),
        ("inputs/energy_eff.csv", ["locs", "techs"], 2),
        ("inputs/names.csv", ["techs"], 1),
        ("outputs/capacity_factor.csv", ["carriers", "locs", "techs", "timesteps"], 3),
        ("outputs/resource_area.csv", ["locs", "techs"], 2),
    ],
)
def test_index_levels(csvfile, idxcols, ncatcols):
    pkgdir = Path("testing/files/mini-ex")
    _, coldict = index_levels(pkgdir / csvfile, idxcols)
    assert all(map(contains, idxcols, coldict))
    cols_w_vals = glom(
        coldict.values(),
        [match({"constraints": {"enum": lambda i: len(i) > 0}, str: str})],
    )
    # ncatcols: only categorical columns are inferred
    assert len(cols_w_vals) == ncatcols


@pytest.mark.parametrize("idx_t", [".csv", ".yaml", ".json"])
def test_pkg_from_index(idx_t):
    meta = {
        "name": "foobarbaz",
        "title": "Foo Bar Baz",
        "keywords": ["foo", "bar", "baz"],
        "license": ["CC0-1.0"],
    }
    idxpath = Path("testing/files/mini-ex/index").with_suffix(idx_t)
    pkgdir, pkg, _ = pkg_from_index(meta, idxpath)
    assert pkgdir == idxpath.parent
    assert len(pkg.descriptor["resources"]) == 5  # number of datasets
    indices = glom(pkg.descriptor, ("resources", Iter().map("schema.primaryKey").all()))
    assert len(indices) == 5
    # FIXME: not sure what else to check


def test_pkg_glossary():
    pkgdir = Path("testing/files/mini-ex")
    pkg = read_pkg(pkgdir / "datapackage.json")
    idx = read_pkg_index(pkgdir / "index.csv")
    glossary = pkg_glossary(pkg, idx)
    assert all(glossary.columns == ["file", "name", "idxcols", "values"])
    assert glossary["values"].apply(lambda i: isinstance(i, list)).all()
    assert len(glossary["file"].unique()) <= glossary.shape[0]


def test_pkg_to_df(pkg, subtests):
    for resource in pkg.resources:
        with subtests.test(msg="default resource", name=resource.name):
            # test target, don't touch this
            df = to_df(resource)
            from_impl = expected_schema(df, type_map={})
            # read from file; strings are read as `object`, remap to `string`
            raw = expected_schema(
                resource, type_map={"object": "string", "int64": "Int64"}
            )
            # impl marks columns as timestamps based on the schema.  similarly
            # as per the schema, remap timestamp columns as timestamps
            ts_cols = [
                field.name
                for field in resource.schema.fields
                if "datetime" in field.type
            ]
            raw.update((col, "datetime64[ns]") for col in ts_cols)
            assert from_impl == raw

        if not ts_cols:  # no timestamps, skip
            continue

        with subtests.test(msg="resource with timestamps", name=resource.name):
            dtype_cmp = df[ts_cols].dtypes == np.dtype("datetime64[ns]")
            assert dtype_cmp.all(axis=None)

    resource = pkg.resources[0]
    field_names = [field.name for field in resource.schema.fields]
    with subtests.test(msg="resource with Index", name=resource.name):
        glom(resource.descriptor, Assign("schema.primaryKey", field_names[0]))
        resource.commit()
        df = to_df(resource)
        # compare columns
        assert list(df.columns) == field_names[1:]
        # check if the right column has been set as index
        assert df.index.name == resource.schema.fields[0].name

    with subtests.test(msg="resource with MultiIndex", name=resource.name):
        glom(resource.descriptor, Assign("schema.primaryKey", field_names[:2]))
        resource.commit()
        df = to_df(resource)
        # compare columns
        assert list(df.columns) == field_names[2:]
        # check if the right column has been set as index
        assert df.index.names == field_names[:2]

    resource = pkg.resources[1]
    with subtests.test(msg="resource with NA", name=resource.name):
        # set new NA value: "sit" from "Lorem ipsum dolor sit amet
        # consectetur adipiscing", TRE - 2nd column
        glom(resource.descriptor, Assign("schema.missingValues", ["", "sit"]))
        resource.commit()
        df = to_df(resource)
        assert df.isna().any(axis=None)

    resource = pkg.resources[0]
    update = {
        "path": resource.descriptor["path"].replace("csv", "txt"),
        "mediatype": resource.descriptor["mediatype"].replace("csv", "plain"),
    }
    resource.descriptor.update(update)
    resource.commit()
    with subtests.test(msg="unsupported resource type", name=resource.name):
        # default behaviour
        with pytest.raises(ValueError, match="unsupported source.+"):
            df = to_df(resource)

        # suppress exceptions
        assert to_df(resource, noexcept=True).empty


def test_pkg_write(pkg, tmp_path_factory, subtests):
    with tmp_path_factory.mktemp("pkgwrite-") as tmpdir:
        zipfile = tmpdir / "testpkg.zip"

        with subtests.test(msg="save as zip", name=f"{zipfile}"):
            write_pkg(pkg, f"{zipfile}")
            assert zipfile.exists()

        tarfile = tmpdir / "testpkg.tar"
        with subtests.test(msg="unsupported archive", name=f"{tarfile}"):
            with pytest.raises(ValueError, match=f".*{tarfile.name}:.+"):
                write_pkg(pkg, f"{tarfile}")
