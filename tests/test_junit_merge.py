"""Tests for junit_merge."""

import xml.etree.ElementTree as ET

import pytest

from junit_merge import Case, Report, merge, merge_files, parse, parse_file, to_xml


def suite(*cases, name="tests", props=None):
    """Build a JUnit document around the given testcase snippets."""
    properties = ""
    if props:
        entries = "".join(f'<property name="{k}" value="{v}"/>' for k, v in props.items())
        properties = f"<properties>{entries}</properties>"
    return (
        f'<testsuite name="{name}" tests="999" failures="999" errors="999" time="999">'
        f"{properties}{''.join(cases)}</testsuite>"
    )


class TestParse:
    def test_reads_a_bare_testsuite(self):
        report = parse(suite('<testcase classname="t" name="a" time="0.5"/>'))
        assert report.tests == 1
        assert report.cases[0].node_id == "t::a"
        assert report.cases[0].time == 0.5

    def test_reads_a_testsuites_wrapper(self):
        xml = "<testsuites>" + suite('<testcase classname="t" name="a"/>') + "</testsuites>"
        assert parse(xml).tests == 1

    def test_reads_several_suites(self):
        xml = (
            "<testsuites>"
            + suite('<testcase classname="t" name="a"/>')
            + suite('<testcase classname="t" name="b"/>')
            + "</testsuites>"
        )
        assert [c.name for c in parse(xml).cases] == ["a", "b"]

    def test_accepts_bytes(self):
        assert parse(suite('<testcase classname="t" name="a"/>').encode()).tests == 1

    def test_status_is_taken_from_the_child_element(self):
        xml = suite(
            '<testcase classname="t" name="ok"/>',
            '<testcase classname="t" name="bad"><failure message="boom">trace</failure></testcase>',
            '<testcase classname="t" name="err"><error message="kaput"/></testcase>',
            '<testcase classname="t" name="skip"><skipped message="no db"/></testcase>',
        )
        statuses = {c.name: c.status for c in parse(xml).cases}
        assert statuses == {"ok": "passed", "bad": "failure", "err": "error", "skip": "skipped"}

    def test_failure_message_and_detail_are_kept(self):
        xml = suite(
            '<testcase classname="t" name="bad"><failure message="boom">trace</failure></testcase>'
        )
        case = parse(xml).cases[0]
        assert case.message == "boom"
        assert case.detail == "trace"

    def test_system_output_is_kept(self):
        xml = suite('<testcase classname="t" name="a"><system-out>printed</system-out></testcase>')
        assert parse(xml).cases[0].system_out == "printed"

    def test_file_and_line_are_kept(self):
        xml = suite('<testcase classname="t" name="a" file="t.py" line="12"/>')
        case = parse(xml).cases[0]
        assert (case.file, case.line) == ("t.py", 12)

    def test_non_numeric_line_is_ignored(self):
        xml = suite('<testcase classname="t" name="a" line="n/a"/>')
        assert parse(xml).cases[0].line is None

    def test_missing_time_is_zero(self):
        xml = suite('<testcase classname="t" name="a"/>')
        assert parse(xml).cases[0].time == 0.0

    def test_garbage_time_is_zero(self):
        xml = suite('<testcase classname="t" name="a" time="fast"/>')
        assert parse(xml).cases[0].time == 0.0

    def test_properties_are_collected(self):
        report = parse(suite('<testcase classname="t" name="a"/>', props={"branch": "main"}))
        assert report.properties == {"branch": "main"}

    def test_namespaced_documents_are_understood(self):
        xml = (
            '<testsuite xmlns="urn:junit" name="t">'
            '<testcase classname="t" name="a"><failure message="m">d</failure></testcase>'
            "</testsuite>"
        )
        case = parse(xml).cases[0]
        assert (case.status, case.message) == ("failure", "m")

    def test_invalid_xml_raises_value_error(self):
        with pytest.raises(ValueError, match="not valid JUnit XML"):
            parse("<testsuite>")

    def test_wrong_root_raises_value_error(self):
        with pytest.raises(ValueError, match="not a JUnit report"):
            parse("<report><testsuite/></report>")

    def test_parse_file(self, tmp_path):
        path = tmp_path / "junit.xml"
        path.write_text(suite('<testcase classname="t" name="a"/>'), encoding="utf-8")
        assert parse_file(path).tests == 1


class TestCounts:
    def test_counts_come_from_the_cases_not_the_attributes(self):
        xml = suite(
            '<testcase classname="t" name="a"/>',
            '<testcase classname="t" name="b"><failure message="m"/></testcase>',
        )
        report = parse(xml)
        assert report.counts() == {"tests": 2, "failures": 1, "errors": 0, "skipped": 0}

    def test_errors_and_skips_are_counted_separately(self):
        xml = suite(
            '<testcase classname="t" name="a"><error message="e"/></testcase>',
            '<testcase classname="t" name="b"><skipped/></testcase>',
        )
        report = parse(xml)
        assert (report.tests, report.errors, report.skipped, report.failures) == (2, 1, 1, 0)

    def test_time_is_summed(self):
        xml = suite(
            '<testcase classname="t" name="a" time="0.25"/>',
            '<testcase classname="t" name="b" time="0.5"/>',
        )
        assert parse(xml).time == 0.75

    def test_empty_report_has_zero_counts(self):
        assert parse(suite()).counts() == {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}


class TestMerge:
    def test_combines_disjoint_cases(self):
        first = parse(suite('<testcase classname="t" name="a"/>'))
        second = parse(suite('<testcase classname="t" name="b"/>'))
        merged = merge([first, second])
        assert [c.name for c in merged.cases] == ["a", "b"]

    def test_keeps_the_worst_outcome_by_default(self):
        passed = parse(suite('<testcase classname="t" name="a"/>'))
        failed = parse(
            suite('<testcase classname="t" name="a"><failure message="boom"/></testcase>')
        )
        merged = merge([passed, failed])
        assert merged.tests == 1
        assert merged.failures == 1

    def test_order_of_inputs_does_not_change_keep_worst(self):
        passed = parse(suite('<testcase classname="t" name="a"/>'))
        errored = parse(suite('<testcase classname="t" name="a"><error message="e"/></testcase>'))
        assert merge([errored, passed]).cases[0].status == "error"
        assert merge([passed, errored]).cases[0].status == "error"

    def test_skipped_beats_passed(self):
        passed = parse(suite('<testcase classname="t" name="a"/>'))
        skipped = parse(suite('<testcase classname="t" name="a"><skipped/></testcase>'))
        assert merge([passed, skipped]).cases[0].status == "skipped"

    def test_keep_first(self):
        first = parse(
            suite('<testcase classname="t" name="a"><failure message="first"/></testcase>')
        )
        second = parse(
            suite('<testcase classname="t" name="a"><failure message="second"/></testcase>')
        )
        assert merge([first, second], on_duplicate="keep-first").cases[0].message == "first"

    def test_keep_last(self):
        first = parse(
            suite('<testcase classname="t" name="a"><failure message="first"/></testcase>')
        )
        second = parse(
            suite('<testcase classname="t" name="a"><failure message="second"/></testcase>')
        )
        assert merge([first, second], on_duplicate="keep-last").cases[0].message == "second"

    def test_keep_both(self):
        first = parse(suite('<testcase classname="t" name="a"/>'))
        second = parse(suite('<testcase classname="t" name="a"/>'))
        assert merge([first, second], on_duplicate="keep-both").tests == 2

    def test_fail_policy_names_the_test(self):
        first = parse(suite('<testcase classname="t" name="a"/>'))
        with pytest.raises(ValueError, match="t::a appears 2 times"):
            merge([first, first], on_duplicate="fail")

    def test_fail_policy_is_silent_without_duplicates(self):
        first = parse(suite('<testcase classname="t" name="a"/>'))
        second = parse(suite('<testcase classname="t" name="b"/>'))
        assert merge([first, second], on_duplicate="fail").tests == 2

    def test_unknown_policy_raises(self):
        with pytest.raises(ValueError, match="duplicate policy must be one of"):
            merge([Report()], on_duplicate="sometimes")

    def test_cases_are_sorted_unless_disabled(self):
        xml = suite('<testcase classname="b" name="z"/>', '<testcase classname="a" name="y"/>')
        report = parse(xml)
        assert [c.node_id for c in merge([report]).cases] == ["a::y", "b::z"]
        assert [c.node_id for c in merge([report], sort=False).cases] == ["b::z", "a::y"]

    def test_properties_are_unioned_first_wins(self):
        first = parse(suite(props={"branch": "main"}))
        second = parse(suite(props={"branch": "dev", "sha": "abc"}))
        merged = merge([first, second])
        assert merged.properties == {"branch": "main", "sha": "abc"}

    def test_single_suite_name_is_kept(self):
        report = parse(suite(name="pytest"))
        assert merge([report]).suite_name == "pytest"

    def test_different_suite_names_merge_to_one_name(self):
        first = parse(suite(name="shard-1"))
        second = parse(suite(name="shard-2"))
        assert merge([first, second]).suite_name == "merged"

    def test_merging_nothing_gives_an_empty_report(self):
        merged = merge([])
        assert (merged.tests, merged.suite_name) == (0, "pytest")

    def test_merge_is_idempotent(self):
        report = parse(suite('<testcase classname="t" name="a" time="1"/>'))
        once = merge([report])
        twice = merge([once])
        assert once.cases == twice.cases
        assert once.time == twice.time


class TestXml:
    def test_round_trips(self):
        report = parse(
            suite(
                '<testcase classname="t" name="ok" time="0.5"/>',
                '<testcase classname="t" name="bad">'
                '<failure message="boom">trace</failure></testcase>',
                '<testcase classname="t" name="skip"><skipped message="no db"/></testcase>',
                props={"branch": "main"},
            )
        )
        reparsed = parse(to_xml(report))
        assert reparsed.cases == report.cases
        assert reparsed.properties == report.properties

    def test_output_is_well_formed_and_declares_its_encoding(self):
        data = to_xml(parse(suite('<testcase classname="t" name="a"/>')))
        assert data.startswith(b"<?xml version='1.0' encoding='utf-8'?>")
        assert ET.fromstring(data).tag == "testsuites"

    def test_counts_in_the_output_match_the_cases(self):
        xml = suite(
            '<testcase classname="t" name="a"/>',
            '<testcase classname="t" name="b"><failure message="m"/></testcase>',
            '<testcase classname="t" name="c"><error message="e"/></testcase>',
        )
        root = ET.fromstring(to_xml(parse(xml)))
        assert (root.get("tests"), root.get("failures"), root.get("errors")) == ("3", "1", "1")
        assert root.find("testsuite").get("tests") == "3"

    def test_the_lying_attributes_of_the_input_do_not_leak(self):
        root = ET.fromstring(to_xml(parse(suite('<testcase classname="t" name="a"/>'))))
        assert root.get("tests") == "1"
        assert root.get("failures") == "0"

    def test_name_can_be_overridden(self):
        root = ET.fromstring(to_xml(parse(suite(name="shard")), name="ci"))
        assert root.find("testsuite").get("name") == "ci"

    def test_system_output_survives(self):
        xml = suite('<testcase classname="t" name="a"><system-out>hello</system-out></testcase>')
        reparsed = parse(to_xml(parse(xml)))
        assert reparsed.cases[0].system_out == "hello"

    def test_unicode_survives(self):
        xml = suite('<testcase classname="t" name="unicodé"/>')
        data = to_xml(parse(xml))
        assert parse(data).cases[0].name == "unicodé"
        assert "unicodé".encode() in data

    def test_markup_in_a_message_is_escaped(self):
        report = Report(cases=[Case(name="a", status="failure", message="<not> & xml")])
        assert parse(to_xml(report)).cases[0].message == "<not> & xml"


class TestMergeFiles:
    def test_writes_the_merged_report(self, tmp_path):
        first = tmp_path / "one.xml"
        second = tmp_path / "two.xml"
        out = tmp_path / "all.xml"
        first.write_text(suite('<testcase classname="t" name="a"/>'), encoding="utf-8")
        second.write_text(suite('<testcase classname="t" name="b"/>'), encoding="utf-8")

        report = merge_files([first, second], out)

        assert report.tests == 2
        assert parse_file(out).tests == 2

    def test_returns_the_report_when_no_output_is_given(self, tmp_path):
        path = tmp_path / "one.xml"
        path.write_text(suite('<testcase classname="t" name="a"/>'), encoding="utf-8")
        assert merge_files([path]).tests == 1

    def test_a_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            merge_files([tmp_path / "nope.xml"])


class TestCommandLine:
    def run(self, argv):
        from junit_merge.__main__ import main

        return main(argv)

    def test_writes_the_output_and_prints_a_summary(self, tmp_path, capsys):
        first = tmp_path / "one.xml"
        second = tmp_path / "two.xml"
        out = tmp_path / "all.xml"
        first.write_text(suite('<testcase classname="t" name="a" time="0.5"/>'), encoding="utf-8")
        second.write_text(
            suite('<testcase classname="t" name="b"><failure message="m"/></testcase>'),
            encoding="utf-8",
        )

        assert self.run([str(first), str(second), "-o", str(out)]) == 0

        assert "2 tests, 1 failures, 0 errors, 0 skipped, 0.50s" in capsys.readouterr().out
        assert parse_file(out).tests == 2

    def test_check_exits_one_when_there_are_failures(self, tmp_path):
        path = tmp_path / "one.xml"
        path.write_text(
            suite('<testcase classname="t" name="a"><failure message="m"/></testcase>'),
            encoding="utf-8",
        )
        assert self.run([str(path), "--check"]) == 1

    def test_check_exits_zero_when_everything_passed(self, tmp_path):
        path = tmp_path / "one.xml"
        path.write_text(suite('<testcase classname="t" name="a"/>'), encoding="utf-8")
        assert self.run([str(path), "--check"]) == 0

    def test_missing_file_exits_two(self, tmp_path, capsys):
        assert self.run([str(tmp_path / "nope.xml")]) == 2
        assert "no such file" in capsys.readouterr().err

    def test_bad_xml_exits_two(self, tmp_path, capsys):
        path = tmp_path / "bad.xml"
        path.write_text("<testsuite>", encoding="utf-8")
        assert self.run([str(path)]) == 2
        assert "not valid JUnit XML" in capsys.readouterr().err

    def test_duplicate_policy_is_forwarded(self, tmp_path, capsys):
        first = tmp_path / "one.xml"
        second = tmp_path / "two.xml"
        first.write_text(suite('<testcase classname="t" name="a"/>'), encoding="utf-8")
        second.write_text(suite('<testcase classname="t" name="a"/>'), encoding="utf-8")
        assert self.run([str(first), str(second), "--on-duplicate", "keep-both"]) == 0
        assert "2 tests" in capsys.readouterr().out


class TestRigour:
    """Suite level output, element input, and the round trip through XML."""

    def test_a_case_system_err_is_read(self):
        report = parse(
            '<testsuite><testcase name="a"><system-out>out</system-out>'
            "<system-err>boom</system-err></testcase></testsuite>"
        )
        assert report.cases[0].system_err == "boom"
        assert report.cases[0].system_out == "out"

    def test_an_element_can_be_parsed(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring('<testsuite><testcase name="a"/></testsuite>')
        assert parse(root).cases[0].name == "a"

    def test_an_unknown_duplicate_policy_is_rejected(self):
        report = parse('<testsuite><testcase name="a"/></testsuite>')
        with pytest.raises(ValueError, match="duplicate policy"):
            merge([report, report], on_duplicate="whatever")

    def test_file_and_line_survive_the_round_trip(self):
        report = parse(
            '<testsuite><testcase name="a" file="t.py" line="12"/></testsuite>'
        )
        from junit_merge import to_xml

        assert 'file="t.py"' in to_xml(report).decode("utf-8")
        assert 'line="12"' in to_xml(report).decode("utf-8")

    def test_system_err_survives_the_round_trip(self):
        report = parse(
            '<testsuite><testcase name="a"><system-err>trace</system-err></testcase></testsuite>'
        )
        from junit_merge import to_xml

        assert "trace" in to_xml(report).decode("utf-8")
