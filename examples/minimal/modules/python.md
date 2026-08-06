# Python Rules

Apply these rules when the task edits Python.

- Type every public boundary, including an explicit return type.
- Let the configured formatter own layout; do not hand-align code.
- Validate untrusted input at the boundary, not throughout the call graph.
- Give every external call a finite deadline and explicit cancellation behavior.
