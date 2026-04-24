# Tool Notes

Only use tools that are necessary for the task. Respect workspace path boundaries.

## GitHub Pull Request Creation
When creating a pull request and you still need the title, source branch, or base branch from the user,
accept ANY format the user sends — plain text, colon-separated, comma-separated, quoted, or backtick-wrapped.
Examples that must ALL work:
- `title: Testing, branch: frontend, base branch: Nick`
- `title: Testing , branch:frontend , 'base branch':Nick`
- `Testing | frontend | Nick`
- `title Testing branch frontend base Nick`
NEVER tell the user to use backtick formatting. Parse whatever they send.

## File Move / Copy Tasks
When the user asks to move or copy files between folders (e.g. "move files from Downloads to Desktop"),
use the host file tools (`host_list_directory`, `host_move_path`, `host_copy_path`) directly.
Do NOT open File Explorer or any GUI application for file operations.
