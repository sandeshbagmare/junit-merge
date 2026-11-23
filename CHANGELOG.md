# Changelog

## 0.1.1

- Badges in the README, shared by the GitHub and PyPI pages.

## 0.1.0

First release.

- `parse`, `parse_file`, `merge`, `merge_files`, `to_xml`.
- Counts are recomputed from the test cases instead of copied from the input.
- Duplicate handling: `keep-worst` (default), `keep-first`, `keep-last`, `keep-both`, `fail`.
- Keeps `message`/detail, `file`/`line`, `system-out`, `system-err` and suite properties.
- `junit-merge` command with `--out`, `--on-duplicate` and `--check`.
