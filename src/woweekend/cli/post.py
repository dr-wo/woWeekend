from __future__ import annotations
from pathlib import Path

from woweekend.config.post_race import PostRaceConfig
from .common import parser, resolve_action


def main() -> None:
    cli = parser("Run Post-Race Retro independently.")
    cli.add_argument("--report-draft", type=Path,
                     help="Assemble an authored report with saved BSF outputs. Also usable with --report-only.")
    args = cli.parse_args()
    raw, config = resolve_action(args, PostRaceConfig, workflow="post_race")
    if config is None:
        return
    from woweekend.workflows.post_race import run_post_race
    run = run_post_race(config, event=args.event, input_config=raw)
    print(run.path)
    if args.report_draft:
        from woweekend.reports.finalize import finalize_post_race_report
        output = finalize_post_race_report(
            bundle_path=run.path / f"report_bundle.{config.report.language}.{config.report.template}",
            draft_path=args.report_draft,
        )
        print(output)


if __name__ == "__main__":
    main()
