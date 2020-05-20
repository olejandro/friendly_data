"""Data package

PS: the coincidential module name is intentional ;)

"""

import json
from pathlib import Path
from typing import Dict, Iterable, Union
from zipfile import ZipFile

from datapackage import Package, Resource
from glom import glom
import pandas as pd

from sark.helpers import import_from

# TODO: compressed files
_source_ts = ["csv", "xls", "xlsx"]  # "sqlite"
_pd_types = {
    "boolean": "bool",
    # "date": "datetime64",
    # "time": "datetime64",
    "datetime": "datetime64",
    "integer": "Int64",
    "number": "float",
    "string": "string",
}


def create_pkg(meta: Dict, resources: Iterable[Union[str, Path, Dict]]):
    """Create a datapackage from metadata and resources.

    Parameters
    ----------
    meta : Dict
        A dictionary with package metadata.
    resources : Iterable[Union[str, Path, Dict]]
        An iterator over different resources.  Resources are just a path to
        files, either as a string or a Path object.  It can also be a
        dictionary as represented by the datapackage library.

    Returns
    -------
    Package
        A fully configured datapackage

    """
    # for an interesting discussion on type hints with unions, see:
    # https://stackoverflow.com/q/60235477/289784
    pkg = Package(meta)
    for res in resources:
        if isinstance(res, (str, Path)):
            if not Path(res).exists():  # pragma: no cover, bad path
                continue
            pkg.infer(f"{res}")
        else:  # pragma: no cover, adding with Dict
            pkg.add_resource(res)
    return pkg


def read_pkg(
    pkg_path: Union[str, Path], extract_dir: Union[str, Path, None] = None
):
    """Read a  datapackage

    If `pkg_path` points to a `datapackage.json` file, read it as is.  If it
    points to a zip archive.  The archive is first extracted before opening it.
    If `extract_dir` is not provided, the current directory of the zip archive
    is used.

    Parameters
    ----------
    pkg_path : Union[str, Path]
        Path to the `datapackage.json` file, or a zip archive

    extract_dir : Union[str, Path]
        Path to which the zip archive is extracted

    Returns
    -------
    Package

    """
    pkg_path = Path(pkg_path)
    if pkg_path.suffix == ".json":
        with open(pkg_path) as pkg_json:
            base_path = f"{Path(pkg_path).parent}"
            return Package(json.load(pkg_json), base_path=base_path)
    elif pkg_path.suffix == ".zip":
        if extract_dir is None:
            extract_dir = pkg_path.parent
        else:
            extract_dir = Path(extract_dir)
        with ZipFile(pkg_path) as pkg_zip:
            pkg_zip.extractall(path=extract_dir)
            with open(extract_dir / "datapackage.json") as pkg_json:
                return Package(json.load(pkg_json), base_path=f"{extract_dir}")
    else:
        raise ValueError(f"{pkg_path}: expecting a JSON or ZIP file")


def update_pkg(pkg, resource, schema_update: Dict, fields: bool = True):
    """Update package resource schema

    Parameters
    ----------
    pkg
        Package object

    resource
        Resource name

    schema_update : Dict

        Updated fields in the schema, if `field` is `False`, can be used to
        update `missingValues`, or `primaryKey`.  When updating the schema, it
        looks like this ('foo'/'bar' are names of the fields being updated):
          {
            "foo": {"name": "foo", "type": "datetime", "format": "default"},
            "bar": {"name": "bar", "type": "integer", "format": "default"}
          }

    fields : bool (default: True)
        If the schema update is a field, or not

    Returns
    -------
    bool
        Return the `Package.valid` flag; `True` if the update was valid.

    """
    desc = pkg.descriptor
    res, *_ = [res for res in desc["resources"] if res["name"] == resource]
    if fields:
        for field in res["schema"]["fields"]:
            if field["name"] in schema_update:
                field.update(schema_update[field["name"]])
    else:
        # FIXME: do the following checks properly
        assert "fields" not in schema_update, "cannot add fields to schema"
        # prevents from adding additional keys
        assert set(schema_update) - {"primaryKey", "missingValues"} == set()
        res["schema"].update(schema_update)
    pkg.commit()
    return pkg.valid


def write_pkg(pkg, pkg_path: Union[str, Path]):
    """Write data package to a zip file

    NOTE: This exists because we want to support saving to other kinds of
    archives like tar, maybe even HDF5, or NetCDF.

    """
    pkg_path = Path(pkg_path)
    if pkg_path.suffix == ".zip":
        pkg.save(pkg_path)
    else:
        raise ValueError(f"{pkg_path}: not a zip file")


def _source_type(source: Union[str, Path]):
    """From a file path, deduce the file type from the extension

    Note: the extension is checked against the list of supported file types

    """
    # FIXME: use file magic
    source_t = Path(source).suffix.strip(".").lower()
    if source_t not in _source_ts:
        raise ValueError(f"unsupported source: {source_t}")
    return source_t


def _schema(resource: Resource, type_map: Dict[str, str]) -> Dict[str, str]:
    """Parse a Resource schema and return types mapped to each column.

    Parameters
    ----------
    resource
        A resource descriptor
    type_map : Dict[str, str]
        A dictionary that maps datapackage type names to pandas types.

    Returns
    -------
    Dict[str, str]

    """
    return dict(
        glom(
            resource,  # target
            (  # spec
                "schema.fields",  # Resource & Schema properties
                [  # fields inside a list
                    (
                        "descriptor",  # Field property
                        lambda t: (  # str -> dtypes understood by pandas
                            t["name"],
                            type_map[t["type"]]
                            # (_type_d[t["type"]], t["format"]),
                        ),
                    )
                ],
            ),
        )
    )


def to_df(resource: Resource, noexcept: bool = False) -> pd.DataFrame:
    """"Reads a data package resource as a `pandas.DataFrame`

    FIXME: only considers 'name' and 'type' in the schema, other options like
    'format', 'missingValues', etc are ignored.

    Parameters
    ----------
    resource : `datapackage.Resource`
        A data package resource object
    noexcept : bool (default: False)
        Whether to suppress an exception

    Returns
    -------
    pandas.DataFrame

    Raises
    ------
    ValueError
        If the resource is not local
        If the source type the resource is pointing to isn't supported

    """
    if not resource.local:
        if noexcept:
            return None
        else:
            raise ValueError(f"{resource.source}: not a local resource")

    pd_readers = {
        "csv": "read_csv",
        "xls": "read_excel",
        "xlsx": "read_excel",
        # "sqlite": "read_sql",
    }
    try:
        reader = import_from(
            "pandas", pd_readers[_source_type(resource.source)]
        )
    except ValueError:
        if noexcept:
            return None
        else:
            raise

    # parse dates
    schema = _schema(resource, _pd_types)
    date_cols = [col for col, col_t in schema.items() if "datetime64" in col_t]
    tuple(map(schema.pop, date_cols))

    # missing values, NOTE: pandas accepts a list of "additional" tokens to be
    # treated as missing values.
    na_values = (
        glom(resource, ("descriptor.schema.missingValues", set))
        - pd._libs.parsers.STR_NA_VALUES
    )
    # FIXME: check if empty set is the same as None

    # FIXME: how to handle constraints? e.g. 'required', 'unique', 'enum', etc
    # see: https://specs.frictionlessdata.io/table-schema/#constraints

    # set 'primaryKey' as index_col, a list is interpreted as a MultiIndex
    index_col = glom(resource, ("descriptor.schema.primaryKey"), default=False)

    return reader(
        resource.source,
        dtype=schema,
        na_values=na_values,
        index_col=index_col,
        parse_dates=date_cols,
    )