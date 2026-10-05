Run build phase $ARGUMENTS.

1. Grep `docs/PHASES.md` for the heading `## Phase $ARGUMENTS` and read only that section.
2. Execute the fenced prompt in that section exactly. CLAUDE.md hard rules override anything else.
3. If the phase needs an open question marked OPEN in PROJECT_STATE.md, stop and say which one.
4. Finish with the section's verification, then commit with the given message. Do not push.
5. Report in at most 15 lines: phase, commit hash, verification result, files touched, doubts.
