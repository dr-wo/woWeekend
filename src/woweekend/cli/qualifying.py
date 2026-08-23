from __future__ import annotations

from woweekend.config.qualifying import QualifyingConfig
from .common import parser, resolve_action


def main() -> None:
    cli = parser("Run Q/SQ preparation independently.")
    args = cli.parse_args()
    raw, config = resolve_action(args, QualifyingConfig, workflow="qualifying_preparation")
    if config is None:
        return
    from woweekend.workflows.qualifying import run_qualifying
    run = run_qualifying(config, event=args.event, input_config=raw)
    print(run.path)


if __name__ == "__main__":
    main()
