# Terminal outcomes

Idle stays compact: installation path, Install, Update and collapsed Details. Once an operation ends, a pinned main-window result panel uses both a large symbol/explicit text and a contrasting background. Install complete, Update complete and Already current have distinct headings. Successful install/update panels show the target and confirmed desktop-shortcut status; the update also shows its version. Missing shortcut confirmation is neutral, and a failed shortcut cannot produce an all-success result.

Failures show a bounded redacted cause and Retry. Cancelled is explicitly cancelled, even if stale success metadata arrives afterwards. New work clears the old result. A prominent blue Exit Installer button is enabled only after the owned worker has completed cleanup. Clicking the window close control during work requests the existing safe cancellation, visibly reports that cleanup is pending, and waits for the worker before destroying the window. No active write is abandoned by the terminal Exit action.

The full redacted log remains available separately. The activity bar stops at the terminal state; the progress text then shows the result rather than a misleading partial phase denominator.

`--smoke-outcome install|updated|current|failed|cancelled` is an explicit display-only QA diagnostic. It never installs or updates, simulates the chosen result, verifies main-window result/Exit visibility and Exit bounds, and invokes Exit to check clean window shutdown.
