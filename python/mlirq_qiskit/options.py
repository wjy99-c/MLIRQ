"""Typed search controls. Ablations are experimental and change guarantees."""
from dataclasses import dataclass
from .errors import InputError


@dataclass(frozen=True)
class MitigationOptions:
    matching: str = "scoped"
    acceptance: str = "componentwise"
    rules: str = "all"
    max_candidates: int = 0
    max_rewrites: int = 0

    def __post_init__(self):
        for name, allowed in (("matching", {"scoped", "global"}),
                              ("acceptance", {"componentwise", "total", "local"}),
                              ("rules", {"all", "diagonal"})):
            if not isinstance(getattr(self, name), str) or getattr(self, name) not in allowed:
                raise InputError("invalid_options", f"{name} must be one of {sorted(allowed)}")
        for name in ("max_candidates", "max_rewrites"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value < 2**63:
                raise InputError("invalid_options", f"{name} must be a nonnegative integer")

        if self.acceptance == "local" and not self.max_candidates:
            raise InputError("invalid_options", "Local-only ablation requires a finite candidate budget")

    def native_options(self):
        return " ".join([
            f"global-matching={int(self.matching == 'global')}",
            f"total-only={int(self.acceptance == 'total')}",
            f"local-only={int(self.acceptance == 'local')}",
            f"diagonal-only={int(self.rules == 'diagonal')}",
            f"max-candidates={self.max_candidates}",
            f"max-rewrites={self.max_rewrites}",
        ])
