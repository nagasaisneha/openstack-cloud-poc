"""Offline tests for scoped, thread-safe simulated lifecycle job records."""

from concurrent.futures import ThreadPoolExecutor
import unittest
from datetime import datetime, timezone
from threading import Barrier

from openstack_cloud_poc.api_contract import LifecycleRequest
from openstack_cloud_poc.identity import FixtureIdentityProvider
from openstack_cloud_poc.jobs import (
    FailureCategory,
    FixtureJobTracker,
    JobErrorCode,
    JobState,
    JobTrackerError,
    _execution_path,
)


PROJECT = "fixture:project-alpha"
INSTANCE = "fixture-instance-000017"
TIMESTAMP = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def create_request(**changes):
    payload = {
        "operation": "compute.create",
        "project_id": PROJECT,
        "image_id": "fixture:image.linux-example",
        "flavor_id": "fixture:flavor.small",
        "network_id": "fixture:network.private-demo",
    }
    payload.update(changes)
    return LifecycleRequest.from_mapping(payload)


def instance_request(operation="compute.start", instance_id=INSTANCE, project_id=PROJECT):
    return LifecycleRequest.from_mapping({
        "operation": operation,
        "project_id": project_id,
        "instance_id": instance_id,
    })


class SequenceClock:
    def __init__(self, *values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


class JobTrackerTests(unittest.TestCase):
    def setUp(self):
        self.identities = FixtureIdentityProvider()
        self.alice = self.identities.authenticate("fixture:alice")
        self.viewer = self.identities.authenticate("fixture:carol")
        self.mallory = self.identities.authenticate("fixture:mallory")
        self.tracker = FixtureJobTracker(clock=lambda: TIMESTAMP)
        self.execution = _execution_path(self.tracker)

    def register_create(self, key=None):
        return self.tracker.register(create_request(), self.alice, key)

    def test_registers_all_supported_mutation_operations_as_simulated(self):
        create = self.register_create()
        self.assertEqual(create.job.operation, "compute.create")
        self.assertEqual(create.job.state, JobState.PENDING)
        self.assertEqual(create.job.project_id, PROJECT)
        self.assertEqual(create.job.actor_id, "fixture:alice")
        self.assertEqual(create.job.created_at, TIMESTAMP)
        self.assertEqual(create.job.updated_at, TIMESTAMP)
        self.assertEqual(create.job.evidence_mode, "simulated")
        self.assertIn("fixture:", create.job.source)

        for operation in ("compute.start", "compute.stop", "compute.delete"):
            with self.subTest(operation=operation):
                result = self.tracker.register(instance_request(operation), self.alice)
                self.assertEqual(result.job.operation, operation)
                self.assertEqual(result.job.instance_id, INSTANCE)
                self.assertTrue(result.created)

    def test_inspection_is_not_a_trackable_mutation_job(self):
        request = instance_request("compute.inspect")
        with self.assertRaises(JobTrackerError) as unsupported:
            self.tracker.register(request, self.alice)
        self.assertEqual(unsupported.exception.code, JobErrorCode.INVALID_OPERATION)

    def test_registration_requires_authenticated_operation_role_and_project(self):
        with self.assertRaises(JobTrackerError) as viewer_denied:
            self.tracker.register(create_request(), self.viewer)
        self.assertEqual(viewer_denied.exception.code, JobErrorCode.AUTHORIZATION_DENIED)

        with self.assertRaises(JobTrackerError) as cross_project:
            self.tracker.register(instance_request(project_id="fixture:project-beta"), self.alice)
        self.assertEqual(cross_project.exception.code, JobErrorCode.AUTHORIZATION_DENIED)

        with self.assertRaises(JobTrackerError) as missing_auth:
            self.tracker.register(create_request(), None)
        self.assertEqual(missing_auth.exception.code, JobErrorCode.AUTHORIZATION_DENIED)

    def test_project_scoped_inspection_and_cross_project_non_disclosure(self):
        registered = self.register_create()
        visible = self.tracker.get(registered.job.job_id, PROJECT, self.viewer)
        self.assertEqual(visible, registered.job)

        with self.assertRaises(JobTrackerError) as other_project:
            self.tracker.get(registered.job.job_id, "fixture:project-beta", self.mallory)
        self.assertEqual(other_project.exception.code, JobErrorCode.NOT_FOUND)
        self.assertEqual(str(other_project.exception), "No matching simulated job exists.")

        with self.assertRaises(JobTrackerError) as no_inspect_role:
            self.tracker.get(registered.job.job_id, PROJECT, self.identities.authenticate("fixture:bob"))
        self.assertEqual(no_inspect_role.exception.code, JobErrorCode.NOT_FOUND)

        with self.assertRaises(JobTrackerError) as missing:
            self.tracker.get("fixture-job-absent", PROJECT, self.viewer)
        self.assertEqual(missing.exception.code, JobErrorCode.NOT_FOUND)
        self.assertNotIn(registered.job.job_id, str(other_project.exception))

    def test_pending_running_succeeded_transitions_and_job_instance_association(self):
        tracker = FixtureJobTracker(
            clock=SequenceClock(
                datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 10, 18, 12, 1, tzinfo=timezone.utc),
                datetime(2026, 10, 18, 12, 2, tzinfo=timezone.utc),
            )
        )
        execution = _execution_path(tracker)
        record = tracker.register(create_request(), self.alice).job
        running = execution.transition(record.job_id, JobState.RUNNING)
        succeeded = execution.transition(record.job_id, JobState.SUCCEEDED, instance_id=INSTANCE)

        self.assertEqual(running.updated_at.minute, 1)
        self.assertEqual(succeeded.updated_at.minute, 2)
        self.assertEqual(succeeded.state, JobState.SUCCEEDED)
        self.assertEqual(succeeded.instance_id, INSTANCE)
        self.assertEqual(succeeded.evidence_mode, "simulated")

    def test_pending_can_fail_with_a_safe_failure_code(self):
        job = self.register_create().job
        failed = self.execution.transition(job.job_id, JobState.FAILED, FailureCategory.TIMEOUT)
        self.assertEqual(failed.state, JobState.FAILED)
        self.assertEqual(failed.failure_code, FailureCategory.TIMEOUT)
        self.assertEqual(failed.failure_category, FailureCategory.TIMEOUT)
        self.assertEqual(failed.to_wire()["failure_category"], "timeout")

    def test_running_can_fail_and_requires_allowlisted_failure_code(self):
        job = self.register_create().job
        self.execution.transition(job.job_id, JobState.RUNNING)
        with self.assertRaises(JobTrackerError) as missing:
            self.execution.transition(job.job_id, JobState.FAILED)
        self.assertEqual(missing.exception.code, JobErrorCode.INVALID_FAILURE_CODE)

        failed = self.execution.transition(job.job_id, JobState.FAILED, FailureCategory.PROVIDER)
        self.assertEqual(failed.state, JobState.FAILED)
        self.assertEqual(failed.failure_code, FailureCategory.PROVIDER)

    def test_all_invalid_transitions_and_terminal_changes_are_rejected(self):
        job = self.register_create().job
        with self.assertRaises(JobTrackerError) as skip:
            self.execution.transition(job.job_id, JobState.SUCCEEDED)
        self.assertEqual(skip.exception.code, JobErrorCode.INVALID_TRANSITION)

        failed = self.execution.transition(job.job_id, JobState.FAILED, FailureCategory.UNKNOWN)
        for state in (JobState.PENDING, JobState.RUNNING, JobState.SUCCEEDED, JobState.FAILED):
            with self.subTest(state=state):
                with self.assertRaises(JobTrackerError) as terminal:
                    self.execution.transition(failed.job_id, state)
                self.assertEqual(terminal.exception.code, JobErrorCode.INVALID_TRANSITION)

        successful_job = self.register_create().job
        self.execution.transition(successful_job.job_id, JobState.RUNNING)
        succeeded = self.execution.transition(successful_job.job_id, JobState.SUCCEEDED)
        with self.assertRaises(JobTrackerError) as succeeded_terminal:
            self.execution.transition(succeeded.job_id, JobState.FAILED, FailureCategory.UNKNOWN)
        self.assertEqual(succeeded_terminal.exception.code, JobErrorCode.INVALID_TRANSITION)

    def test_transitions_are_not_exposed_on_public_tracker_surface(self):
        self.assertFalse(hasattr(self.tracker, "transition"))
        self.assertTrue(callable(self.execution.transition))

    def test_identifier_provenance_rejects_real_or_unqualified_server_ids(self):
        job = self.register_create().job
        self.execution.transition(job.job_id, JobState.RUNNING)
        for identifier in ("nova-server-123", "123e4567-e89b-12d3-a456-426614174000", "instance-1"):
            with self.subTest(identifier=identifier):
                with self.assertRaises(JobTrackerError) as invalid:
                    self.execution.transition(job.job_id, JobState.SUCCEEDED, instance_id=identifier)
                self.assertEqual(invalid.exception.code, JobErrorCode.INVALID_REQUEST)

        valid = self.execution.transition(job.job_id, JobState.SUCCEEDED, instance_id=INSTANCE)
        wire = valid.to_wire()
        self.assertEqual(wire["instance_id"], INSTANCE)
        self.assertEqual(wire["evidence_mode"], "simulated")
        self.assertNotIn("resource_id", wire)
        self.assertNotIn("nova_server_id", wire)

        for identifier in ("nova-server-123", "123e4567-e89b-12d3-a456-426614174000"):
            with self.subTest(registration_identifier=identifier):
                with self.assertRaises(JobTrackerError) as invalid:
                    self.tracker.register(instance_request("compute.start", identifier), self.alice)
                self.assertEqual(invalid.exception.code, JobErrorCode.INVALID_REQUEST)

    def test_same_idempotency_key_and_request_returns_existing_job(self):
        first = self.tracker.register(create_request(), self.alice, "request-001")
        duplicate = self.tracker.register(create_request(), self.alice, "request-001")

        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.job, first.job)

    def test_idempotency_key_is_scoped_by_project_and_operation(self):
        create = self.tracker.register(create_request(), self.alice, "same-key")
        start = self.tracker.register(instance_request("compute.start"), self.alice, "same-key")
        self.assertNotEqual(create.job.job_id, start.job.job_id)
        self.assertTrue(start.created)

    def test_conflicting_payload_with_same_idempotency_key_is_rejected(self):
        self.tracker.register(create_request(), self.alice, "request-002")
        with self.assertRaises(JobTrackerError) as conflict:
            self.tracker.register(
                create_request(network_id="fixture:network.other"),
                self.alice,
                "request-002",
            )
        self.assertEqual(conflict.exception.code, JobErrorCode.IDEMPOTENCY_CONFLICT)
        self.assertNotIn("network.other", str(conflict.exception))

    def test_idempotency_key_validation(self):
        for key in ("", " \n ", "x" * 256):
            with self.subTest(key_length=len(key)):
                with self.assertRaises(JobTrackerError) as invalid:
                    self.tracker.register(create_request(), self.alice, key)
                self.assertEqual(invalid.exception.code, JobErrorCode.INVALID_REQUEST)

    def test_concurrent_duplicate_registration_creates_one_job(self):
        worker_count = 16
        barrier = Barrier(worker_count)

        def register():
            barrier.wait()
            return self.tracker.register(create_request(), self.alice, "concurrent-key")

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            results = list(executor.map(lambda _: register(), range(worker_count)))

        self.assertEqual(len({result.job.job_id for result in results}), 1)
        self.assertEqual(sum(result.created for result in results), 1)

    def test_concurrent_state_transition_is_atomic(self):
        job = self.register_create().job
        worker_count = 12
        barrier = Barrier(worker_count)

        def start():
            barrier.wait()
            try:
                self.execution.transition(job.job_id, JobState.RUNNING)
                return "updated"
            except JobTrackerError as error:
                self.assertEqual(error.code, JobErrorCode.INVALID_TRANSITION)
                return "rejected"

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            outcomes = list(executor.map(lambda _: start(), range(worker_count)))
        self.assertEqual(outcomes.count("updated"), 1)
        self.assertEqual(outcomes.count("rejected"), worker_count - 1)
        self.assertEqual(self.tracker.get(job.job_id, PROJECT, self.viewer).state, JobState.RUNNING)

    def test_clock_must_be_aware_and_monotonic(self):
        naive = FixtureJobTracker(
            clock=lambda: datetime(2026, 10, 18),
        )
        with self.assertRaises(JobTrackerError) as bad_clock:
            naive.register(create_request(), self.alice)
        self.assertEqual(bad_clock.exception.code, JobErrorCode.INVALID_CLOCK)

        backwards = FixtureJobTracker(
            clock=SequenceClock(
                datetime(2026, 10, 18, 12, 1, tzinfo=timezone.utc),
                datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
            )
        )
        created = backwards.register(create_request(), self.alice).job
        with self.assertRaises(JobTrackerError) as non_monotonic:
            _execution_path(backwards).transition(created.job_id, JobState.RUNNING)
        self.assertEqual(non_monotonic.exception.code, JobErrorCode.INVALID_CLOCK)
        self.assertEqual(backwards.get(created.job_id, PROJECT, self.viewer).state, JobState.PENDING)


if __name__ == "__main__":
    unittest.main()
