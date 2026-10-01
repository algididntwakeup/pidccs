from typing import List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.project import Project
from ..models.project_assignment import ProjectAssignment
from ..models.user import User
from ..schemas.bima import BimaProjectRecord


class BimaProjectSyncService:
    @staticmethod
    async def sync_user_projects(
        db: AsyncSession,
        user: User,
        bima_projects: List[BimaProjectRecord],
    ) -> List[Project]:
        synced: List[Project] = []
        # BIMA may expose the same project more than once through joined
        # assignment rows. Collapse duplicates before creating pending rows so
        # autoflush=False cannot enqueue duplicate user assignments.
        unique_projects = {project.bima_project_id: project for project in bima_projects}
        incoming_ids = set(unique_projects)

        for record in unique_projects.values():
            project = await db.scalar(
                select(Project).where(Project.bima_project_id == record.bima_project_id)
            )
            if project is None:
                project = Project(
                    bima_project_id=record.bima_project_id,
                    name=record.name,
                    description=record.description or "",
                    owner_id=None,
                    user_id=user.id,
                )
                db.add(project)
                await db.flush()
            else:
                project.name = record.name
                project.description = record.description or ""

            # Only locally known BIMA identities can be assigned. The callback
            # creates these mappings as users arrive from the identity provider.
            assigned_users = (await db.scalars(
                select(User).where(User.bima_user_id.in_(record.assigned_bima_user_ids))
            )).all() if record.assigned_bima_user_ids else []
            if all(assigned_user.id != user.id for assigned_user in assigned_users):
                assigned_users.append(user)

            existing_assignments = (await db.scalars(
                select(ProjectAssignment).where(ProjectAssignment.project_id == project.id)
            )).all()
            assigned_user_ids = {assigned_user.id for assigned_user in assigned_users}
            by_user_id = {assignment.user_id: assignment for assignment in existing_assignments}
            for assigned_user in assigned_users:
                if assigned_user.id not in by_user_id:
                    db.add(ProjectAssignment(
                        project_id=project.id,
                        user_id=assigned_user.id,
                        source="bima",
                    ))
            for assignment in existing_assignments:
                if assignment.source == "bima" and assignment.user_id not in assigned_user_ids:
                    await db.delete(assignment)

            synced.append(project)

        # Revoke only this user's stale BIMA assignments. Ownership and other
        # users' assignments are preserved.
        stale_assignments = await db.execute(
            select(ProjectAssignment)
            .join(Project, Project.id == ProjectAssignment.project_id)
            .where(
                ProjectAssignment.user_id == user.id,
                ProjectAssignment.source == "bima",
                Project.bima_project_id.is_not(None),
            )
        )
        for assignment in stale_assignments.scalars():
            project = await db.get(Project, assignment.project_id)
            if project and project.bima_project_id not in incoming_ids:
                await db.delete(assignment)

        await db.flush()
        return synced
