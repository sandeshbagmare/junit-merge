# junit-merge

[![PyPI](https://img.shields.io/pypi/v/junit-merge.svg)](https://pypi.org/project/junit-merge/)
[![Python](https://img.shields.io/pypi/pyversions/junit-merge.svg)](https://pypi.org/project/junit-merge/)
[![tests](https://github.com/sandeshbagmare/junit-merge/actions/workflows/ci.yml/badge.svg)](https://github.com/sandeshbagmare/junit-merge/actions/workflows/ci.yml)
[![license](https://img.shields.io/pypi/l/junit-merge.svg)](https://github.com/sandeshbagmare/junit-merge/blob/main/LICENSE)

One JUnit XML file from many, with counts you can trust.

```console
python -m junit_merge check-reports/*.xml --out junit.xml
```

```python
from junit_merge import merge_files

report = merge_files(["shard-1.xml", "shard-2.xml"], "junit.xml")
print(report.tests, report.failures)
```

Sharded CI jobs, `pytest --junitxml` per matrix leg and retried flaky tests all leave
you with several reports when the dashboard wants one. Concatenating the XML is
invalid, and adding up each file's `tests` and `failures` attributes double counts
every test a shard re-ran.

This package parses the reports, rebuilds the merged document from the test cases
themselves, and recomputes every count, so the numbers always match the contents.

## Duplicate tests

A test that appears in more than one report (the usual result of a retry) is
resolved with `on_duplicate`:

| policy | behaviour |
| ------ | --------- |
| `keep-worst` (default) | keep the failure or error over the pass; a test that failed anywhere stays failed |
| `keep-first` | keep the first occurrence |
| `keep-last` | keep the last occurrence |
| `keep-both` | keep every occurrence |
| `fail` | raise, naming the test, instead of guessing |

Keeping the worst outcome is the safe default: a flaky retry cannot hide a real
failure from the report.

## Install

```console
pip install junit-merge
```

## API

```python
from junit_merge import merge, merge_files, parse, parse_file, to_xml

report = parse_file("shard-1.xml")  # Report
merged = merge([report, parse_file("shard-2.xml")], on_duplicate="keep-worst")
merged.tests, merged.failures, merged.errors, merged.skipped, merged.time
data = to_xml(merged)  # bytes, valid <testsuites> document
```

`parse` accepts `str`, `bytes`, a path is handled by `parse_file`, and a document
name-spaced by tools like Jenkins is understood. Invalid XML raises `ValueError` with
the reason, not a bare `ParseError`.

## What is preserved

- `classname`, `name`, `time`, `file`, `line`
- `<failure>`, `<error>` and `<skipped>` including `message` and the text body
- `<system-out>` and `<system-err>`
- suite `<properties>`, first value winning on a clash

Cases are sorted by class and name by default, so two runs of the same merge produce
identical bytes; pass `sort=False` to keep the input order.

## What is not

- It does not run tests, retry them or upload anything: it merges files.
- Attributes nobody sets from test cases (`timestamp`, `hostname`) are not invented.
- A report whose contents contradict each other is trusted on the cases, which is
  the only part that can be verified.

## Development

```console
python -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest
```

## License

MIT.
