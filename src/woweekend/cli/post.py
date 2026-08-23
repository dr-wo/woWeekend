from __future__ import annotations

from woweekend.config.post_race import PostRaceConfig
from .common import parser, resolve_action


def main() -> None:
    cli = parser("Run Post-Race Retro independently.")
    args = cli.parse_args()
    raw, config = resolve_action(args, PostRaceConfig, workflow="post_race")
    if config is None:
        return
    from woweekend.workflows.post_race import run_post_race
    run = run_post_race(config, event=args.event, input_config=raw)
    print(run.path)


if __name__ == "__main__":
    main()
