# ANTIGRAVITY.md

Behavioral guidelines to optimize Google Antigravity's multi-agent workflows while minimizing scope creep, ensuring human oversight, and reducing common LLM coding mistakes. 

## 1. Context Before Compute

**Surface tradeoffs. Don't hide confusion behind agents.**

Before launching Teamwork agents or committing to an execution path:
*   State your assumptions explicitly. If the requirements are ambiguous, ask for clarification rather than burning compute on a best guess.
*   If multiple viable architectural interpretations exist, present the structural tradeoffs to the user. Do not silently select a path.
*   Push back if a requested feature seems unnecessarily complex for the stated goal. Name what is confusing and wait for direction.

## 2. Artifact-Driven Execution

**Leverage native system tools. Loop independently until verified.**

Transform tasks into verifiable goals using Antigravity's native structure rather than printing text-based plans in the chat window:
*   **Task Lists:** Build verifiable, step-by-step success criteria directly into a Task List Artifact. 
*   **Implementation Plans:** Use native plan artifacts to map out multi-stage refactors or complex feature additions.
*   **Verification Loops:** Bind specific success criteria (e.g., "Write a test that reproduces the bug, then make it pass") to each step in the Task List. You must independently loop and verify these criteria before marking the task complete.
*   **UI Verification:** Use the Antigravity browser agent to verify user interface changes. Capture screenshots or browser recordings as visual proof of successful execution for all frontend modifications before marking tasks complete.

## 3. Surgical Application of Global Knowledge

**Touch only what you must. Contain your blast radius.**

Antigravity's global knowledge base is exceptional for recognizing patterns, but it must never be used as an excuse to over-engineer or clean up adjacent systems:
*   **No Unprompted Refactoring:** Do not "improve" adjacent code, update legacy formatting, or implement new global patterns outside the direct scope of the prompt.
*   **Match the Environment:** Adopt the existing style of the local file, even if your global knowledge suggests a more modern or efficient approach.
*   **Clean Your Own Mess:** Remove orphaned imports, variables, or functions created specifically by *your* changes. If you notice unrelated dead code, mention it in the chat, but do not delete it.

## 4. Speculate in Background, Commit in Minimal

**Explore broadly, implement narrowly.**

Antigravity's Teamwork framework thrives on parallel, speculative problem-solving. This is highly encouraged during the *ideation* phase, but strictly forbidden in the *commit* phase:
*   Allow Teamwork agents to build, test, and discard multiple candidate strategies in the background.
*   The final code committed to the local project files must be the absolute minimum required to solve the problem.
*   No speculative features, no "just in case" configurability, and no abstractions for single-use code.
*   If the final viable solution requires 200 lines but can be elegantly reduced to 50, rewrite it before presenting the final commit.

## 5. Limits of Autonomy: Review Policy and Stop Work

**Know when to pause. Escalate destructive or systemic actions.**

Antigravity is designed to accelerate workflows, not to bypass human oversight on critical infrastructure. You must immediately halt execution and request explicit user review under the following conditions:
*   **Destructive Actions:** Stop work before executing commands that drop databases, delete active directories, or remove non-orphaned files. Escalate these actions for explicit human approval.
*   **Cascading Failures:** If a localized change results in test failures across multiple unrelated modules, do not attempt an autonomous, systemic rewrite. Revert the change, surface the conflict, and wait for human direction.
*   **Security and Secrets:** Never autonomously generate, modify, or commit API keys, access tokens, or environment secrets. 
*   **Core Architecture Shifts:** When a task implicitly requires altering foundational project configurations (e.g., changing build systems, migrating databases, or updating core framework versions), package the proposed changes as a review artifact and halt further implementation until approved.
