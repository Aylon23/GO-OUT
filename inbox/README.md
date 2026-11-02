# Inbox

Drop researched files here (`*.json`). The app checks this folder every few seconds while it runs:

- Scenario specs (`"kind": "scenario"`, see `docs/scenario-spec.schema.json`)
- Earnings briefs (`"kind": "earnings_brief"`, see `docs/earnings-brief.schema.json`)

Imported files move to `processed/`. Files with errors move to `failed/`, with a `.error.txt` next to them
explaining what to fix. The folder can be changed in Settings.
