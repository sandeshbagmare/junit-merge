"""Merge JUnit XML reports from sharded or retried test runs into one report.

A CI pipeline that splits tests across jobs, or retries flaky ones, ends up with
several JUnit XML files when the reporter wants exactly one. Concatenating them
double counts, and adding up the ``tests``/``failures`` attributes of each file is
wrong as soon as a shard re-ran a test.

    >>> from junit_merge import merge, parse
    >>> one = parse('<testsuite name="s"><testcase classname="t" name="a"/></testsuite>')
    >>> two = parse('<testsuite name="s"><testcase classname="t" name="b"/></testsuite>')
    >>> merge([one, two]).tests
    2

The merged report is rebuilt from the test cases, so its counts always match what it
contains. Duplicate cases, which are what a retry produces, are resolved by keeping
the worst outcome by default: a test that failed in one shard and passed in another
is reported as a failure rather than quietly disappearing.

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Case",
    "DuplicatePolicy",
    "MergedReport",
    "Report",
    "case_key",
    "merge",
    "merge_files",
    "parse",
    "parse_file",
]

__version__ = "0.1.1"

#: Worst last, so ``max`` over these ranks picks the outcome that matters most.
SEVERITY = {"passed": 0, "skipped": 1, "failure": 2, "error": 3}

POLICIES = ("keep-worst", "keep-first", "keep-last", "keep-both", "fail")


@dataclass(frozen=True)
class Case:
    """One ``<testcase>``."""

    name: str
    classname: str = ""
    time: float = 0.0
    status: str = "passed"
    message: str = ""
    detail: str = ""
    file: str = ""
    line: int | None = None
    system_out: str = ""
    system_err: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return case_key(self)

    @property
    def node_id(self) -> str:
        return f"{self.classname}::{self.name}" if self.classname else self.name


def case_key(case: Case) -> tuple[str, str]:
    """The identity of a test across shards: its class and its name."""
    return (case.classname, case.name)


@dataclass
class Report:
    """A parsed report: test cases plus the properties of the run."""

    cases: list[Case] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    suite_name: str = "pytest"

    @property
    def tests(self) -> int:
        return len(self.cases)

    @property
    def failures(self) -> int:
        return sum(1 for c in self.cases if c.status == "failure")

    @property
    def errors(self) -> int:
        return sum(1 for c in self.cases if c.status == "error")

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.cases if c.status == "skipped")

    @property
    def time(self) -> float:
        return round(sum(c.time for c in self.cases), 6)

    def to_xml(self, *, name: str | None = None) -> bytes:
        """Serialise this report as a JUnit ``<testsuites>`` document."""
        return to_xml(self, name=name)

    def counts(self) -> dict[str, int]:
        return {
            "tests": self.tests,
            "failures": self.failures,
            "errors": self.errors,
            "skipped": self.skipped,
        }


MergedReport = Report
DuplicatePolicy = str


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text


def _float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value else default
    except (TypeError, ValueError):
        return default


def _case_from(element: ET.Element) -> Case:
    status, message, detail = "passed", "", ""
    for child in element:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag in ("failure", "error"):
            status = tag
            message = child.get("message", "")
            detail = _text(child)
            break
        if tag == "skipped":
            status = "skipped"
            message = child.get("message", "")
            detail = _text(child)
            break
    system_out = ""
    system_err = ""
    for child in element:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "system-out":
            system_out = _text(child)
        elif tag == "system-err":
            system_err = _text(child)
    line = element.get("line")
    return Case(
        name=element.get("name", ""),
        classname=element.get("classname", ""),
        time=_float(element.get("time")),
        status=status,
        message=message,
        detail=detail,
        file=element.get("file", ""),
        line=int(line) if line and line.isdigit() else None,
        system_out=system_out,
        system_err=system_err,
    )


def _walk_suites(root: ET.Element) -> Iterable[ET.Element]:
    tag = root.tag.rsplit("}", 1)[-1]
    if tag == "testsuite":
        yield root
        return
    if tag == "testsuites":
        for suite in root:
            if suite.tag.rsplit("}", 1)[-1] == "testsuite":
                yield from _walk_suites(suite)


def parse(source: str | bytes | ET.Element) -> Report:
    """Parse a JUnit XML document (text, bytes or an element) into a :class:`Report`."""
    if isinstance(source, ET.Element):
        root = source
    else:
        try:
            root = ET.fromstring(source)
        except ET.ParseError as exc:
            raise ValueError(f"not valid JUnit XML: {exc}") from None
    if root.tag.rsplit("}", 1)[-1] not in ("testsuite", "testsuites"):
        raise ValueError(f"not a JUnit report: root element is <{root.tag}>")

    cases: list[Case] = []
    properties: dict[str, str] = {}
    suite_name = ""
    for suite in _walk_suites(root):
        if not suite_name:
            suite_name = suite.get("name", "")
        for props in suite:
            if props.tag.rsplit("}", 1)[-1] != "properties":
                continue
            for entry in props:
                key = entry.get("name", "")
                if key and key not in properties:
                    properties[key] = entry.get("value", "")
        for child in suite:
            if child.tag.rsplit("}", 1)[-1] == "testcase":
                cases.append(_case_from(child))
    return Report(cases=cases, properties=properties, suite_name=suite_name or "pytest")


def parse_file(path: str | os.PathLike[str]) -> Report:
    """Parse a JUnit XML file into a :class:`Report`."""
    return parse(Path(path).read_bytes())


def _resolve(duplicates: list[Case], policy: DuplicatePolicy) -> list[Case]:
    if len(duplicates) == 1:
        return duplicates
    if policy == "keep-first":
        return duplicates[:1]
    if policy == "keep-last":
        return duplicates[-1:]
    if policy == "keep-both":
        return duplicates
    if policy == "keep-worst":
        worst = max(duplicates, key=lambda c: SEVERITY.get(c.status, 0))
        return [worst]
    raise ValueError(f"duplicate policy must be one of {', '.join(POLICIES)}")


def merge(
    reports: Sequence[Report],
    *,
    on_duplicate: DuplicatePolicy = "keep-worst",
    sort: bool = True,
) -> Report:
    """Merge reports, resolving cases that appear in more than one of them.

    ``on_duplicate`` is one of ``keep-worst`` (default), ``keep-first``,
    ``keep-last``, ``keep-both`` or ``fail``.
    """
    if on_duplicate not in POLICIES:
        raise ValueError(f"duplicate policy must be one of {', '.join(POLICIES)}")

    order: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[Case]] = {}
    properties: dict[str, str] = {}
    suite_names: list[str] = []
    for report in reports:
        for case in report.cases:
            if case.key not in grouped:
                grouped[case.key] = []
                order.append(case.key)
            grouped[case.key].append(case)
        for key, value in report.properties.items():
            properties.setdefault(key, value)
        if report.suite_name and report.suite_name not in suite_names:
            suite_names.append(report.suite_name)

    cases: list[Case] = []
    for key in order:
        duplicates = grouped[key]
        if len(duplicates) > 1 and on_duplicate == "fail":
            raise ValueError(
                f"{duplicates[0].node_id} appears {len(duplicates)} times; "
                "pass on_duplicate to choose which outcome to keep"
            )
        cases.extend(_resolve(duplicates, on_duplicate))

    if sort:
        cases.sort(key=lambda c: (c.classname, c.name))

    name = suite_names[0] if suite_names else "pytest"
    if len(suite_names) > 1:
        name = "merged"
    return Report(cases=cases, properties=properties, suite_name=name)


def merge_files(
    paths: Sequence[str | os.PathLike[str]],
    out: str | os.PathLike[str] | None = None,
    *,
    on_duplicate: DuplicatePolicy = "keep-worst",
    sort: bool = True,
) -> Report:
    """Parse several files, merge them, and write the result when ``out`` is given."""
    report = merge([parse_file(p) for p in paths], on_duplicate=on_duplicate, sort=sort)
    if out is not None:
        Path(out).write_bytes(report.to_xml())
    return report


def _set_attr(element: ET.Element, key: str, value: object) -> None:
    element.set(key, str(value))


def to_xml(report: Report, *, name: str | None = None) -> bytes:
    """Serialise a report. Counts are computed from the cases, never copied."""
    suite_name = name or report.suite_name
    root = ET.Element("testsuites")
    for key, value in report.counts().items():
        _set_attr(root, key, value)
    _set_attr(root, "time", report.time)
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", suite_name)
    for key, value in report.counts().items():
        _set_attr(suite, key, value)
    _set_attr(suite, "time", report.time)

    if report.properties:
        props = ET.SubElement(suite, "properties")
        for key, value in report.properties.items():
            ET.SubElement(props, "property", {"name": key, "value": value})

    for case in report.cases:
        element = ET.SubElement(suite, "testcase", {"name": case.name})
        if case.classname:
            element.set("classname", case.classname)
        if case.time:
            element.set("time", f"{case.time:g}")
        if case.file:
            element.set("file", case.file)
        if case.line is not None:
            element.set("line", str(case.line))
        if case.status in ("failure", "error"):
            child = ET.SubElement(element, case.status)
            if case.message:
                child.set("message", case.message)
            child.text = case.detail
        elif case.status == "skipped":
            child = ET.SubElement(element, "skipped")
            if case.message:
                child.set("message", case.message)
            child.text = case.detail
        if case.system_out:
            ET.SubElement(element, "system-out").text = case.system_out
        if case.system_err:
            ET.SubElement(element, "system-err").text = case.system_err

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
