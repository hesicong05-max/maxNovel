"""Project-scoped dependent cleanup for the explicit project delete flow.

These rows deliberately use RESTRICT foreign keys to protect their referenced
objects during ordinary resource mutations.  Once the authenticated project
DELETE endpoint has locked the owning project, they must be removed child-first
inside the same transaction so the project-level cascade can complete.
"""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction import LoreExtractionCandidate
from app.models.foreshadow import (
    ForeshadowFact,
    ForeshadowLifecycle,
    ForeshadowLifecycleEvent,
    ForeshadowPlanItem,
)
from app.models.lore import LoreElementCreateOperation
from app.models.planning import PlanningLoreAssignment


async def delete_project_relational_dependents(
    db: AsyncSession,
    project_id: str,
) -> None:
    """Delete only project-owned rows that block the root project cascade.

    The caller owns commit/rollback and must already have authenticated the
    owner and acquired the project UPDATE lock.  Keep this list explicit: a new
    RESTRICT bridge requires its own reviewed addition and regression test.
    """

    child_first_models = (
        ForeshadowLifecycleEvent,
        ForeshadowFact,
        ForeshadowPlanItem,
        ForeshadowLifecycle,
        PlanningLoreAssignment,
        LoreExtractionCandidate,
        LoreElementCreateOperation,
    )
    for model in child_first_models:
        await db.execute(
            delete(model)
            .where(model.project_id == project_id)
            .execution_options(synchronize_session=False)
        )
