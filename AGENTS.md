
# Specialized Agents

## Skeleton0 — First-Principles Tutor

Use the custom `skeleton0` agent whenever the user explicitly
requests "Skeleton0", "skeleton0", or asks for the Skeleton0 role.

Delegate the educational task to the `skeleton0` subagent.

When returning its response:
- Preserve the Skeleton0 explanation structure.
- Do not replace its reasoning with a short summary.
- Retain technical details, examples, and corrections.
- For follow-up questions, retain the context of the original exercise.
- Respect requests for hints rather than complete solutions.

Do not invoke Skeleton0 for unrelated programming or repository
tasks unless the user requests that role.

