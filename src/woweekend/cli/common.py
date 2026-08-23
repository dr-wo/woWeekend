from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Type

from wodata.weekend_runs import WeekendRunStore
from wodata.events import parse_event_id

from woweekend.config.common import load_json_config, write_template
from woweekend.reports.bundle import regenerate_report_bundle


def parser(description: str) -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=description)
    value.add_argument("--event", help="Canonical event identifier YYYY-RR, e.g. 2026-07. Legacy suffixed IDs are accepted temporarily.")
    value.add_argument("--config", type=Path)
    value.add_argument("--generate-config", type=Path)
    value.add_argument("--validate-config", type=Path)
    value.add_argument("--report-only", metavar="RUN_ID")
    value.add_argument("--language", choices=("zh-CN", "en-GB"))
    return value


def resolve_action(args, config_type: Type[Any], *, workflow: str):
    if args.generate_config:
        write_template(args.generate_config, config_type.template())
        print(args.generate_config)
        return None, None
    validation_path = args.validate_config
    if validation_path:
        raw = load_json_config(validation_path)
        config_type.parse(raw)
        print(f"Valid: {validation_path}")
        return None, None
    if args.report_only:
        if not args.event:
            raise SystemExit("--event is required with --report-only")
        event_id, _, _ = parse_event_id(args.event)
        run = WeekendRunStore.load(event=event_id, workflow=workflow, run_id=args.report_only)
        output = regenerate_report_bundle(run_path=run.path, language=args.language or "zh-CN")
        print(output)
        return None, None
    if not args.event or not args.config:
        raise SystemExit("--event and --config are required to run the workflow")
    raw = load_json_config(args.config)
    config = config_type.parse(raw)
    if args.language:
        from dataclasses import replace
        config = replace(config, report=replace(config.report, language=args.language))
    return raw, config
