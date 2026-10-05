# Rule files

One YAML file per rule set and effective period, schema in docs/CONTRACT.md "## C4".
File name: `<rule_set>__<version>.yaml`, e.g. `gr-dist-network__2026-02-01.yaml`.
Never edit a VERIFIED file in place when the regulator changes a value: add a new file
with a new `effective_from` and close the old one with `effective_to`.
