"""Fixed parameters of the eval, derived rather than guessed.

A leash that is set by feel becomes part of the measurement without anyone
deciding that it should be. `recursion_limit` was a literal 12 at three call
sites; the first real run then exhausted it, and the eval recorded that as agent
behaviour. It was partly ours.

MEASURED_STEP_FLOOR is the smallest recursion_limit at which a known-good
ScriptedModel trajectory completes — measured, not computed, by running the
positive-control script (load_experiment -> run_qc -> assign_groups ->
compute_sleep -> final answer) at successive limits until it finished:

    limit=9  GraphRecursionError
    limit=10 completes          <- floor

LangGraph counts one super-step per node, so a trajectory of n tool calls costs
2n + 2 (agent and tools alternate, plus the closing agent turn). The old literal
12 was therefore *exactly* the floor for a five-call trajectory: enough to
succeed on a flawless first attempt and nothing left for a single retry. A model
that made one bad call could not recover, and the run was scored as if the limit
had not been a factor.

The multiplier is the room a real model gets to explore and recover beyond the
minimum. Three is deliberate: it buys roughly two extra passes of the minimal
trajectory, so a run that still exhausts the limit is a genuine failure to
converge rather than a budget we set too tight. Raising it makes crashes rarer
and slower; lowering it measures our leash instead of the agent.
"""

MEASURED_STEP_FLOOR = 10
RECURSION_MULTIPLIER = 3
RECURSION_LIMIT = MEASURED_STEP_FLOOR * RECURSION_MULTIPLIER   # 30
