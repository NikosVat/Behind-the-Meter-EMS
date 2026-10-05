Review the uncommitted diff (or the last commit if the tree is clean) against:
1. CLAUDE.md hard rules (actuation, invented constants, synthetic prices, UTC, Decimal,
   secrets, network in tests, fail-closed auth)
2. the CONTRACT.md sections the changed code touches (grep them)
3. new branches without tests, and edits to LEGACY tariff_engine/ that extend it
Report as: file:line, rule broken, suggested fix. No edits. At most 25 lines.
