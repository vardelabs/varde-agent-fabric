# shellcheck shell=bash
# The most agentic steps an OpenCode review may take before OpenCode forces a
# text-only turn, so a review that keeps reading files still writes findings.
# A slow model was measured at about 19 s a step. 16 steps at that pace take
# about 300 s, which leaves room for the final turn inside a 600 s review
# budget, and overruns it only above about 35 s a step.
review_step_limit=16
