# Roster and Names route card

Read this card only for a handoff touching roster services or private identity tools.
The agent reads pseudonymized roster context and edits local student settings over MCP.
The console's Names page is a private read-only identity table with safety tools.

## Owners

| Concern | Owner |
|---|---|
| Roster acquisition, normalization and local student updates | `api/roster_service.py` |
| MCP read/preview/apply wrappers | `api/mcp_server/tools.py` |
| Local roster settings | `api/platform_services/config/roster.py` |
| Private Names table and safety endpoints | `api/webui/routes/names.py` |
| Names presentation and search | `api/webui/templates/names.html`, `api/webui/static/names.js`, `api/webui/static/pages/names.css` |

`GET /api/names?course_id=...` returns pseudonym, real name and sections for one
course. It exposes no roster edit fields. Protected names, scrub tests, who-is-who
export and vault backup remain console-only private tools.

## Privacy and writes

- Names, IDs, sections, accommodations, pseudonyms, monitoring and notes are private
  student data. Never place them in source, fixtures, generic logs or support output.
- `get_roster`, `preview_roster_student_change` and `apply_roster_student_change`
  use pseudonyms. A student's read returns its expected digest; preview freezes a
  patch and apply refuses if the settings moved. A null `extra_time`, `monitored` or
  `classroom_profile` clears that field.
- MCP roster writes call `roster_service.update_student`, with no Web UI route
  import. The service owns validation and extra-time/monitored handling.
- The MCP patch can only add nicknames (`add_nicknames`). Replacing them stays outside
  the patch surface because it would overwrite the teacher's scrub-coverage list.
- Canvas Expert has no student-to-tier mapping and does not edit group membership.
  Teachers assign tier assignments to students or pods in Canvas.
- Identity exports and vault backups remain private workspace artifacts.

## Verification

Use the handoff's focused MCP roster and service tests. Names presentation changes
require a rendered `/names` check with course selection, search and safety tools,
and zero new browser console errors.
