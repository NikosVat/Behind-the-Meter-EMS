"""Constrained Load Optimization and Closed-Loop Decision-Support Subsystem for Behind-the-Meter EMS.

Provides:
- Equipment constraint models (DefrostLoad, HVACLoad, ProductionBatchLoad, BESSLoad).
- Mixed-Integer Linear Programming solver using SciPy HiGHS (ConstrainedLoadSolver).
- Actionable operational decision support recommendation engine (DecisionSupportEngine).
- Post-intervention closed-loop telemetry audit verifier (ClosedLoopVerifier).
"""

from optimization_engine.decision_support import (
    ClosedLoopVerifier,
    DecisionSupportEngine,
)
from optimization_engine.models import (
    ActionRecommendation,
    BESSLoad,
    DefrostLoad,
    HVACLoad,
    OptimizationProblem,
    PriorityLevel,
    ProductionBatchLoad,
    RecommendationCategory,
    RecommendationStatus,
    ScheduleResult,
    VerificationRecord,
    VerificationStatus,
)
from optimization_engine.solver import ConstrainedLoadSolver

__all__ = [
    "ActionRecommendation",
    "BESSLoad",
    "ClosedLoopVerifier",
    "ConstrainedLoadSolver",
    "DecisionSupportEngine",
    "DefrostLoad",
    "HVACLoad",
    "OptimizationProblem",
    "PriorityLevel",
    "ProductionBatchLoad",
    "RecommendationCategory",
    "RecommendationStatus",
    "ScheduleResult",
    "VerificationRecord",
    "VerificationStatus",
]
