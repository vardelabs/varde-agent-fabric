# shellcheck shell=bash
# The most agentic steps an OpenCode review may take before OpenCode forces a
# text-only turn, so a review that keeps reading files still writes findings.
# On a large reviewed tree a step took 40 to 50 s. 10 steps at 50 s take 500 s,
# which leaves room for the final turn inside a 600 s review budget. A step
# count cannot bound wall time on its own; a caller that needs a hard bound
# must also stop the run at a time point.
review_step_limit=10
