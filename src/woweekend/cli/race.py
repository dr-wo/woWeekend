from __future__ import annotations

from woweekend.config.race import RaceConfig
from .common import parser, resolve_action


def main() -> None:
    cli = parser("Run Race/Sprint Race preparation independently.")
    cli.add_argument("--pit-loss-total", type=float, help="Explicit whole GREEN pit loss in seconds; overrides JSON.")
    cli.add_argument("--pit-in-s3", type=float, help="Explicit green pit-in S3 loss (seconds); overrides JSON.")
    cli.add_argument("--pit-out-s1", type=float, help="Explicit green pit-out S1 loss (seconds); overrides JSON.")
    args = cli.parse_args()
    raw, config = resolve_action(args, RaceConfig, workflow="race_preparation")
    if config is None:
        return
    from woweekend.workflows.race import run_race
    run = run_race(config, event=args.event, input_config=raw, pit_loss_total=args.pit_loss_total, pit_in_s3=args.pit_in_s3, pit_out_s1=args.pit_out_s1)
    print(run.path)


if __name__ == "__main__":
    main()
