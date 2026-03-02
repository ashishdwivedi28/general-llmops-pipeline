"""CLI entry point — parses YAML config, validates, and runs the matching pipeline job."""

import argparse
import json
import sys

from llmops_pipeline import settings
from llmops_pipeline.io import configs

parser = argparse.ArgumentParser(description="Run an LLMOps pipeline job from YAML config.")
parser.add_argument("files", nargs="*", help="YAML config files (merged in order).")
parser.add_argument("-e", "--extras", nargs="*", default=[], help="Inline config overrides.")
parser.add_argument("-s", "--schema", action="store_true", help="Print JSON Schema and exit.")


def main(argv: list[str] | None = None) -> int:
    """Parse config → validate via Pydantic → enter job context → run."""
    args = parser.parse_args(argv)
    if args.schema:
        schema = settings.MainSettings.model_json_schema()
        json.dump(schema, sys.stdout, indent=4)
        return 0
    files = [configs.parse_file(f) for f in args.files]
    strings = [configs.parse_string(s) for s in args.extras]
    if not files and not strings:
        raise RuntimeError("No config files provided. Usage: llmops confs/feature_engineering.yaml")
    config = configs.merge_configs([*files, *strings])
    object_ = configs.to_object(config)
    setting = settings.MainSettings.model_validate(object_)
    with setting.job as runner:
        runner.run()
    return 0
