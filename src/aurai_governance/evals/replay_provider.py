"""Promptfoo Python provider that replays recorded outputs (used to test the export/import pipeline offline).

It is copied next to the exported config by ``aurai-governance evals export --replay-mode``. It never calls a model.
A model-graded rubric cannot be evaluated offline, so the grader call is answered with "not evaluated".
"""

import os

import yaml


def call_api(prompt, options, context):
    config = (options or {}).get("config", {})
    if config.get("grader"):  # a grader call for an llm-rubric assertion
        # No verdict on purpose: Promptfoo reports a grader error, which the importer records as "not evaluated".
        return {"output": {"reason": "replay has no judge model"}}
    base = os.path.dirname(os.path.abspath(__file__))
    case = context["vars"]["aurai_case"]
    with open(os.path.join(base, config["replay"]), encoding="utf-8") as handle:
        replay = yaml.safe_load(handle)
    return {"output": replay["outputs"][case][config.get("variant", "pass")]}
