# End-to-end validation workflow

## Objective

Verify that one fresh supported producer session-context event reaches the dashboard's project attribution through the managed adapter, the inbox, and `introspection.session_projects`.

## Guidance

- Load the canonical session-context contract before validating fields or tuple completeness.
- Start a fresh supported producer session only when runtime validation is part of the request.
- Confirm the managed adapter wrote the inbox record, then that `agent-introspection facts schedule status` shows the sync job loaded with no backlog after the next minute.
- Query `introspection.session_projects FINAL` by `event_id` and confirm the producer, session ID, event type, project ID, project name, and home-redacted root match the inbox record.
- Confirm the session's facts (usage, tasks, or tool calls with the same `session_id`) join `introspection.session_project` to that project, and that the Pipeline view's project attribution counts the session's tasks as attributed.
- Reject missing, duplicate, conflicting, inferred, or uncorrelated results; a non-Git workspace must appear only as a rejection in `session_project_rejections`.
- Record the producer, session ID, event ID, project tuple, fact rows joined, and dashboard result.
