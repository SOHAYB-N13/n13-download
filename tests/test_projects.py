"""Project layer — domain rules, persistence and the three delete policies.

The rule this file exists to protect: a project is a *container*, not a folder
name.  Renaming one must never break the tasks inside it, deleting one must never
silently destroy the user's files, and every destructive path must be explicit.
The tests below therefore assert on the database and the filesystem, not on the
service's own return values alone.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import DEFAULT_PROJECT_ID, Database
from core.store import TaskStore
from core.task import DownloadTask
from projects.models import (
    CompletionAction,
    DeleteMode,
    ProjectNotFound,
    ProjectState,
    ProjectStatus,
    ProjectValidationError,
    normalize_name,
    validate_name,
)
from projects.service import ProjectService


def _task(task_id: str, *, status: str = "Queued", directory: str = "", filename: str = "") -> DownloadTask:
    return DownloadTask(
        id=task_id,
        url=f"https://x/{task_id}.zip",
        directory=directory or "C:/dl",
        filename=filename or f"{task_id}.zip",
        category="Archives",
        status=status,
        total_size=1000,
        downloaded_size=250,
    )


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "n13.db")
        self.store = TaskStore(self.db)
        self.service = ProjectService(self.db, self.store)
        self.service.ensure_default()

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _save(self, task: DownloadTask, project_id: str) -> None:
        payload = task.to_dict()
        payload["project_id"] = project_id
        self.store.save_task(payload)


# --------------------------------------------------------------------------- #
# Name rules
# --------------------------------------------------------------------------- #


class NameRulesTest(unittest.TestCase):
    def test_internal_whitespace_is_collapsed(self):
        self.assertEqual(normalize_name("  My   Project \n"), "My Project")

    def test_a_name_is_required(self):
        with self.assertRaises(ProjectValidationError) as ctx:
            validate_name("   ")
        self.assertEqual(ctx.exception.code, "name_required")
        self.assertEqual(ctx.exception.field, "name")

    def test_a_name_may_not_be_absurdly_long(self):
        with self.assertRaises(ProjectValidationError) as ctx:
            validate_name("x" * 200)
        self.assertEqual(ctx.exception.code, "name_too_long")

    def test_names_are_compared_case_insensitively(self):
        with self.assertRaises(ProjectValidationError) as ctx:
            validate_name("movies", ["Movies"])
        self.assertEqual(ctx.exception.code, "name_duplicate")

    def test_whitespace_differences_do_not_create_a_second_name(self):
        with self.assertRaises(ProjectValidationError):
            validate_name("My  Project", ["My Project"])

    def test_a_clean_name_is_returned_trimmed(self):
        self.assertEqual(validate_name("  Movies  "), "Movies")


# --------------------------------------------------------------------------- #
# Default project
# --------------------------------------------------------------------------- #


class DefaultProjectTest(_Base):
    def test_ensure_default_is_idempotent(self):
        first = self.service.ensure_default()
        second = self.service.ensure_default()
        self.assertEqual(first.id, DEFAULT_PROJECT_ID)
        self.assertEqual(second.id, DEFAULT_PROJECT_ID)
        self.assertEqual(self.service._repo.count(), 1)

    def test_the_default_project_cannot_be_deleted(self):
        with self.assertRaises(ProjectValidationError) as ctx:
            self.service.delete(DEFAULT_PROJECT_ID)
        self.assertEqual(ctx.exception.code, "project_not_deletable")
        self.assertTrue(self.service._repo.exists(DEFAULT_PROJECT_ID))

    def test_the_default_project_is_flagged_for_the_ui(self):
        default = self.service.ensure_default()
        self.assertTrue(default.is_default)
        self.assertFalse(default.can_delete)

    def test_a_created_project_is_deletable(self):
        project = self.service.create("Movies")
        self.assertFalse(project.is_default)
        self.assertTrue(project.can_delete)


# --------------------------------------------------------------------------- #
# Creation, persistence, renaming
# --------------------------------------------------------------------------- #


class CreationTest(_Base):
    def test_create_persists_every_field(self):
        project = self.service.create(
            "Movies",
            "Weekend films",
            str(self.tmp / "movies"),
            3,
            {"enabled": True, "start": "23:00", "stop": "07:00", "days": [5, 6]},
            "shutdown",
        )
        loaded = self.service.get(project.id)
        self.assertEqual(loaded.name, "Movies")
        self.assertEqual(loaded.description, "Weekend films")
        self.assertEqual(loaded.max_concurrent, 3)
        self.assertEqual(loaded.completion_action, CompletionAction.SHUTDOWN)
        self.assertTrue(loaded.schedule.enabled)
        self.assertEqual(loaded.schedule.start, "23:00")
        self.assertEqual(loaded.schedule.stop, "07:00")
        self.assertEqual(loaded.schedule.days, [5, 6])

    def test_a_duplicate_name_is_rejected_at_the_service_layer(self):
        self.service.create("Movies")
        with self.assertRaises(ProjectValidationError) as ctx:
            self.service.create("movies")
        self.assertEqual(ctx.exception.code, "name_duplicate")

    def test_a_project_survives_a_full_reopen(self):
        project = self.service.create("Movies", "desc", "", 4, None, "none")
        self.db.close()

        self.db = Database(self.tmp / "n13.db")
        self.store = TaskStore(self.db)
        self.service = ProjectService(self.db, self.store)

        loaded = self.service.get(project.id)
        self.assertEqual(loaded.name, "Movies")
        self.assertEqual(loaded.description, "desc")
        self.assertEqual(loaded.max_concurrent, 4)

    def test_created_at_and_updated_at_are_set(self):
        project = self.service.create("Movies")
        self.assertGreater(project.created_at, 0)
        self.assertGreaterEqual(project.updated_at, project.created_at)

    def test_an_unknown_project_raises_not_found(self):
        with self.assertRaises(ProjectNotFound) as ctx:
            self.service.get("prj_does_not_exist")
        self.assertEqual(ctx.exception.code, "project_not_found")

    def test_concurrency_is_clamped_to_the_ceiling(self):
        project = self.service.create("Movies", max_concurrent=9999)
        self.assertLessEqual(project.max_concurrent, 50)

    def test_a_negative_concurrency_becomes_the_inherit_value(self):
        project = self.service.create("Movies", max_concurrent=-5)
        self.assertEqual(project.max_concurrent, 0)


class ListingOrderTest(_Base):
    """The group tab strip renders ``list_views()`` in order, so it must be stable.

    Ordering by ``updated_at`` would make a tab jump the moment its group was
    renamed or paused — the one thing a row of tabs must never do.  The Default
    group stays pinned first because it owns every legacy, unassigned download.
    """

    def test_the_default_project_comes_first(self):
        self.service.create("Zebra")
        self.assertEqual(self.service.list_views()[0].project.id, DEFAULT_PROJECT_ID)

    def test_projects_are_listed_in_creation_order(self):
        for name in ("First", "Second", "Third"):
            self.service.create(name)
        names = [view.project.name for view in self.service.list_views()]
        self.assertEqual(names[1:], ["First", "Second", "Third"])

    def test_touching_a_project_does_not_reorder_the_list(self):
        first = self.service.create("First")
        self.service.create("Second")
        before = [view.project.id for view in self.service.list_views()]
        self.service.update(first.id, status="paused")
        self.service.update(first.id, name="First renamed")
        self.assertEqual([view.project.id for view in self.service.list_views()], before)


class RenameTest(_Base):
    def test_renaming_keeps_the_id_and_the_task_links(self):
        project = self.service.create("Movies")
        self._save(_task("t1"), project.id)

        renamed = self.service.rename(project.id, "Films")

        self.assertEqual(renamed.id, project.id, "a rename must not change the id")
        self.assertEqual(renamed.name, "Films")
        self.assertEqual(self.store.load_task("t1")["project_id"], project.id)
        self.assertEqual(self.service.counts(project.id).total, 1)

    def test_renaming_does_not_move_files(self):
        """The name is a label; the destination folder is the path."""
        folder = self.tmp / "movies"
        project = self.service.create("Movies", directory=str(folder))
        self.service.rename(project.id, "Films")
        self.assertEqual(self.service.get(project.id).directory, str(folder))

    def test_renaming_to_an_existing_name_is_rejected(self):
        self.service.create("Movies")
        other = self.service.create("Films")
        with self.assertRaises(ProjectValidationError) as ctx:
            self.service.rename(other.id, "Movies")
        self.assertEqual(ctx.exception.code, "name_duplicate")

    def test_renaming_a_project_to_its_own_name_is_allowed(self):
        project = self.service.create("Movies")
        renamed = self.service.rename(project.id, "Movies")
        self.assertEqual(renamed.name, "Movies")

    def test_a_rename_touches_updated_at(self):
        project = self.service.create("Movies")
        renamed = self.service.rename(project.id, "Films")
        self.assertGreaterEqual(renamed.updated_at, project.updated_at)


# --------------------------------------------------------------------------- #
# Task association
# --------------------------------------------------------------------------- #


class TaskAssociationTest(_Base):
    def test_tasks_are_counted_per_project(self):
        a = self.service.create("A")
        b = self.service.create("B")
        self._save(_task("t1", status="Complete"), a.id)
        self._save(_task("t2", status="Downloading"), a.id)
        self._save(_task("t3", status="Queued"), b.id)

        counts_a = self.service.counts(a.id)
        self.assertEqual(counts_a.total, 2)
        self.assertEqual(counts_a.completed, 1)
        self.assertEqual(counts_a.active, 1)

        counts_b = self.service.counts(b.id)
        self.assertEqual(counts_b.total, 1)
        self.assertEqual(counts_b.queued, 1)

    def test_a_task_can_be_moved_between_projects(self):
        a = self.service.create("A")
        b = self.service.create("B")
        self._save(_task("t1"), a.id)

        self.store.reassign_project(a.id, b.id)

        self.assertEqual(self.service.counts(a.id).total, 0)
        self.assertEqual(self.service.counts(b.id).total, 1)

    def test_project_id_for_task_reports_the_owner(self):
        a = self.service.create("A")
        self._save(_task("t1"), a.id)
        self.assertEqual(self.service.project_id_for_task("t1"), a.id)

    def test_has_active_tasks_ignores_finished_work(self):
        project = self.service.create("A")
        self._save(_task("t1", status="Complete"), project.id)
        self.assertFalse(self.service.has_active_tasks(project.id))
        self._save(_task("t2", status="Downloading"), project.id)
        self.assertTrue(self.service.has_active_tasks(project.id))

    def test_a_legacy_task_without_a_project_is_visible_in_the_default(self):
        payload = _task("legacy").to_dict()
        payload["project_id"] = ""
        self.store.save_task(payload)
        self.assertEqual(self.service.counts(DEFAULT_PROJECT_ID).total, 1)


# --------------------------------------------------------------------------- #
# Derived state
# --------------------------------------------------------------------------- #


class DerivedStateTest(_Base):
    def test_an_empty_project_reports_empty(self):
        project = self.service.create("A")
        view = self.service.get_view(project.id)
        self.assertEqual(view.state, ProjectState.EMPTY)
        self.assertEqual(view.state_reason, "no_tasks")

    def test_a_running_transfer_wins_over_everything_but_a_user_pause(self):
        project = self.service.create("A")
        self._save(_task("t1", status="Downloading"), project.id)
        self.assertEqual(self.service.get_view(project.id).state, ProjectState.RUNNING)

    def test_a_user_pause_wins_over_a_running_transfer(self):
        project = self.service.create("A")
        self._save(_task("t1", status="Downloading"), project.id)
        self.service.pause(project.id)
        view = self.service.get_view(project.id)
        self.assertEqual(view.state, ProjectState.PAUSED_BY_USER)
        self.assertEqual(view.state_reason, "project_paused")

    def test_queued_work_reports_queued(self):
        project = self.service.create("A")
        self._save(_task("t1", status="Queued"), project.id)
        view = self.service.get_view(project.id)
        self.assertEqual(view.state, ProjectState.QUEUED)
        self.assertEqual(view.state_reason, "waiting_for_slot")

    def test_a_failure_is_reported_when_nothing_is_pending(self):
        project = self.service.create("A")
        self._save(_task("t1", status="Failed"), project.id)
        view = self.service.get_view(project.id)
        self.assertEqual(view.state, ProjectState.FAILED)
        self.assertEqual(view.state_reason, "has_failures")

    def test_all_finished_reports_completed(self):
        project = self.service.create("A")
        self._save(_task("t1", status="Complete"), project.id)
        view = self.service.get_view(project.id)
        self.assertEqual(view.state, ProjectState.COMPLETED)
        self.assertEqual(view.state_reason, "all_complete")

    def test_the_view_payload_is_one_flat_shape(self):
        project = self.service.create("A")
        payload = self.service.get_view(project.id).to_dict()
        for key in ("id", "name", "status", "state", "state_reason", "schedule",
                    "schedule_open", "counts", "can_delete", "is_default",
                    "effective_concurrency"):
            self.assertIn(key, payload)

    def test_counts_payload_reports_progress(self):
        project = self.service.create("A")
        self._save(_task("t1", status="Complete"), project.id)
        counts = self.service.counts(project.id).to_dict()
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["downloaded_bytes"], 250)
        self.assertEqual(counts["total_bytes"], 1000)
        self.assertEqual(counts["progress_percent"], 25.0)

    def test_pause_and_resume_flip_the_status(self):
        project = self.service.create("A")
        self.assertEqual(self.service.pause(project.id).status, ProjectStatus.PAUSED)
        self.assertEqual(self.service.resume(project.id).status, ProjectStatus.ACTIVE)


# --------------------------------------------------------------------------- #
# Delete policies
# --------------------------------------------------------------------------- #


class DeleteKeepTasksTest(_Base):
    def test_keep_tasks_moves_them_to_the_default_project(self):
        project = self.service.create("A")
        self._save(_task("t1"), project.id)
        self._save(_task("t2"), project.id)

        result = self.service.delete(project.id, DeleteMode.KEEP_TASKS)

        self.assertTrue(result["ok"])
        self.assertFalse(self.service._repo.exists(project.id))
        self.assertEqual(self.service.counts(DEFAULT_PROJECT_ID).total, 2)
        self.assertIsNotNone(self.store.load_task("t1"))

    def test_keep_tasks_is_the_default_mode(self):
        project = self.service.create("A")
        self._save(_task("t1"), project.id)
        self.service.delete(project.id)
        self.assertEqual(self.service.counts(DEFAULT_PROJECT_ID).total, 1)


class DeleteRecordsTest(_Base):
    def test_delete_records_removes_rows_and_keeps_files(self):
        folder = self.tmp / "downloads"
        folder.mkdir()
        artifact = folder / "t1.zip"
        artifact.write_bytes(b"payload")

        project = self.service.create("A", directory=str(folder))
        self._save(_task("t1", directory=str(folder)), project.id)

        result = self.service.delete(project.id, DeleteMode.DELETE_RECORDS)

        self.assertTrue(result["ok"])
        self.assertEqual(result["files_deleted"], 0)
        self.assertIsNone(self.store.load_task("t1"))
        self.assertTrue(artifact.exists(), "delete_records must never touch the disk")

    def test_deleting_a_busy_project_is_refused(self):
        project = self.service.create("A")
        self._save(_task("t1", status="Downloading"), project.id)
        with self.assertRaises(ProjectValidationError) as ctx:
            self.service.delete(project.id, DeleteMode.DELETE_RECORDS)
        self.assertEqual(ctx.exception.code, "project_busy")
        self.assertTrue(self.service._repo.exists(project.id))

    def test_an_unknown_delete_mode_is_refused(self):
        project = self.service.create("A")
        with self.assertRaises(ProjectValidationError) as ctx:
            self.service.delete(project.id, "nuke_everything")
        self.assertEqual(ctx.exception.code, "invalid_delete_mode")


class DeleteFilesTest(_Base):
    def _project_with_file(self):
        folder = self.tmp / "downloads"
        folder.mkdir()
        artifact = folder / "t1.zip"
        artifact.write_bytes(b"payload")
        part = folder / "t1.zip.part1"
        part.write_bytes(b"half")
        project = self.service.create("A", directory=str(folder))
        payload = _task("t1", directory=str(folder)).to_dict()
        payload["project_id"] = project.id
        payload["resolved_path"] = str(artifact)
        self.store.save_task(payload)
        return project, artifact, part

    def test_delete_files_needs_explicit_confirmation(self):
        project, artifact, _part = self._project_with_file()
        with self.assertRaises(ProjectValidationError) as ctx:
            self.service.delete(project.id, DeleteMode.DELETE_FILES)
        self.assertEqual(ctx.exception.code, "delete_files_not_confirmed")
        self.assertTrue(artifact.exists(), "an unconfirmed delete must change nothing")
        self.assertTrue(self.service._repo.exists(project.id))

    def test_confirmed_delete_files_removes_the_file_and_its_parts(self):
        project, artifact, part = self._project_with_file()

        result = self.service.delete(
            project.id, DeleteMode.DELETE_FILES, confirm_delete_files=True
        )

        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["files_deleted"], 2)
        self.assertFalse(artifact.exists())
        self.assertFalse(part.exists(), "partial segment files belong to the task too")
        self.assertIsNone(self.store.load_task("t1"))
        self.assertFalse(self.service._repo.exists(project.id))

    def test_delete_files_without_a_directory_is_refused(self):
        """With no folder, N13 cannot tell which files are the project's."""
        project = self.service.create("A")
        payload = _task("t1").to_dict()
        payload["project_id"] = project.id
        self.store.save_task(payload)

        with self.assertRaises(ProjectValidationError) as ctx:
            self.service.delete(project.id, DeleteMode.DELETE_FILES, confirm_delete_files=True)
        self.assertEqual(ctx.exception.code, "files_require_directory")
        self.assertTrue(self.service._repo.exists(project.id))

    def test_a_file_outside_the_project_folder_is_never_deleted(self):
        """A hand-edited path must not turn delete into an arbitrary file wipe."""
        folder = self.tmp / "downloads"
        folder.mkdir()
        outside = self.tmp / "precious.txt"
        outside.write_bytes(b"do not delete")

        project = self.service.create("A", directory=str(folder))
        payload = _task("t1", directory=str(folder)).to_dict()
        payload["project_id"] = project.id
        payload["resolved_path"] = str(outside)
        self.store.save_task(payload)

        result = self.service.delete(
            project.id, DeleteMode.DELETE_FILES, confirm_delete_files=True
        )

        self.assertTrue(result["ok"])
        self.assertTrue(outside.exists(), "a path outside the project root must be skipped")

    def test_a_file_shared_with_another_project_is_skipped(self):
        folder = self.tmp / "shared"
        folder.mkdir()
        artifact = folder / "t1.zip"
        artifact.write_bytes(b"payload")

        a = self.service.create("A", directory=str(folder))
        b = self.service.create("B", directory=str(folder))
        payload = _task("t1", directory=str(folder)).to_dict()
        payload["project_id"] = a.id
        payload["resolved_path"] = str(artifact)
        self.store.save_task(payload)
        self._save(_task("t2", directory=str(folder), filename="t1.zip"), b.id)

        self.service.delete(a.id, DeleteMode.DELETE_FILES, confirm_delete_files=True)

        self.assertTrue(artifact.exists(), "another project still references this file")


class DeleteReferentialIntegrityTest(_Base):
    def test_deleting_a_project_leaves_no_orphan_task_rows(self):
        project = self.service.create("A")
        self._save(_task("t1"), project.id)
        self.service.delete(project.id, DeleteMode.DELETE_RECORDS)
        rows = self.store.list_tasks_by_project(project.id)
        self.assertEqual(rows, [])

    def test_deleting_a_project_does_not_touch_other_projects(self):
        a = self.service.create("A")
        b = self.service.create("B")
        self._save(_task("t1"), a.id)
        self._save(_task("t2"), b.id)

        self.service.delete(a.id, DeleteMode.DELETE_RECORDS)

        self.assertEqual(self.service.counts(b.id).total, 1)
        self.assertTrue(self.service._repo.exists(b.id))

    def test_the_default_project_survives_deleting_a_neighbour(self):
        project = self.service.create("A")
        self._save(_task("t1"), project.id)
        self.service.delete(project.id)
        self.assertTrue(self.service._repo.exists(DEFAULT_PROJECT_ID))


if __name__ == "__main__":
    unittest.main()
