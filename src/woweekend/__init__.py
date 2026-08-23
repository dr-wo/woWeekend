"""Race-weekend orchestration without analytical reimplementation."""


def run_qualifying(*args, **kwargs):
    from .workflows.qualifying import run_qualifying as implementation

    return implementation(*args, **kwargs)


def run_race(*args, **kwargs):
    from .workflows.race import run_race as implementation

    return implementation(*args, **kwargs)


def run_post_race(*args, **kwargs):
    from .workflows.post_race import run_post_race as implementation

    return implementation(*args, **kwargs)

__all__ = ["run_post_race", "run_qualifying", "run_race"]
