"""Integration tests for the simulated asynchronous lifecycle path."""

from concurrent.futures import ThreadPoolExecutor
import socket
import threading
import unittest
from unittest.mock import patch

from openstack_cloud_poc.api_contract import LifecycleAcceptedResponse
from openstack_cloud_poc.execution import (
    ExecutionError,
    ExecutionErrorCode,
    FixtureOperationFailure,
    LocalExecutionEngine,
)
from openstack_cloud_poc.identity import FixtureIdentityProvider
from openstack_cloud_poc.instance_lifecycle import (
    FixtureInstanceProvider,
    InstanceState,
    LifecycleResult,
)
from openstack_cloud_poc.jobs import (
    FailureCategory,
    FixtureJobTracker,
    JobErrorCode,
    JobState,
    JobTrackerError,
)
from openstack_cloud_poc.management_service import (
    AsynchronousLifecycleManagementService,
    ServiceErrorCode,
    SynchronousManagementService,
)


PROJECT = "fixture:project-alpha"
IMAGE = "fixture:image.linux-example"
FLAVOR = "fixture:flavor.small"
NETWORK = "fixture:network.private-demo"


def create_payload(project_id=PROJECT):
    return {
        "operation": "compute.create",
        "project_id": project_id,
        "image_id": IMAGE,
        "flavor_id": FLAVOR,
        "network_id": NETWORK,
    }


def instance_payload(operation, instance_id, project_id=PROJECT):
    return {
        "operation": operation,
        "project_id": project_id,
        "instance_id": instance_id,
    }


class GatedCreateProvider(FixtureInstanceProvider):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def create(self, project_id, image_id, flavor_id, network_id):
        self.calls += 1
        record = super().create(project_id, image_id, flavor_id, network_id)
        self.started.set()
        if not self.release.wait(timeout=3):
            raise TimeoutError("test gate timeout")
        return record


class GatedTransitionProvider(FixtureInstanceProvider):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.transition_calls = 0

    def transition(self, instance_id, target, project_id=None):
        self.transition_calls += 1
        self.started.set()
        if not self.release.wait(timeout=3):
            raise TimeoutError("test gate timeout")
        return super().transition(instance_id, target, project_id)


class PartialCreateProvider(FixtureInstanceProvider):
    def create(self, project_id, image_id, flavor_id, network_id):
        record = super().create(project_id, image_id, flavor_id, network_id)
        raise FixtureOperationFailure(FailureCategory.PROVIDER, record.instance_id)


class AsyncLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.identities = FixtureIdentityProvider()
        self.alice = self.identities.authenticate("fixture:alice")
        self.viewer = self.identities.authenticate("fixture:carol")
        self.mallory = self.identities.authenticate("fixture:mallory")
        self.provider = FixtureInstanceProvider()
        self.tracker = FixtureJobTracker()
        self.executor = LocalExecutionEngine(self.tracker, max_workers=2, max_pending=8)
        self.service = AsynchronousLifecycleManagementService(
            tracker=self.tracker,
            executor=self.executor,
            provider=self.provider,
        )

    def tearDown(self):
        self.executor.close()

    def accepted(self, result):
        self.assertIsNone(result.error)
        self.assertIsInstance(result.response, LifecycleAcceptedResponse)
        self.assertEqual(result.response.evidence_mode.value, "simulated")
        self.assertEqual(result.response.status.value, "accepted")
        self.assertEqual(result.response.source, "fixture:local asynchronous lifecycle; simulated only")
        return result.response

    def job_for(self, accepted_response, authentication=None, project_id=PROJECT):
        result = self.service.get_job(
            accepted_response.job_id,
            project_id,
            authentication or self.alice,
        )
        self.assertIsNone(result.error)
        self.assertIsNone(result.response)
        self.assertIsNotNone(result.job)
        return result.job

    def test_create_is_accepted_then_runs_and_records_fixture_instance(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network call")) as connect:
            result = self.service.handle(create_payload(), self.alice, "create-001")
            response = self.accepted(result)
            self.assertNotIn("instance_id", response.to_wire())
            self.assertNotIn("resource_id", response.to_wire())
            self.assertIsNotNone(result.execution_handle)
            completed = result.execution_handle.result(timeout=3)
            connect.assert_not_called()

        self.assertEqual(completed.state, JobState.SUCCEEDED)
        self.assertEqual(completed.operation, "compute.create")
        self.assertEqual(completed.project_id, PROJECT)
        self.assertEqual(completed.actor_id, "fixture:alice")
        self.assertTrue(completed.instance_id.startswith("fixture-instance-"))
        self.assertEqual(completed.evidence_mode, "simulated")
        instance = self.provider.inspect(completed.instance_id)
        self.assertEqual(instance.project_id, PROJECT)
        self.assertEqual(instance.state, InstanceState.ACTIVE)
        self.assertEqual(self.job_for(response).state, JobState.SUCCEEDED)

    def test_create_failure_marks_job_failed_without_false_resource_claim(self):
        self.provider.fail_next = RuntimeError("private provider detail")
        result = self.service.handle(create_payload(), self.alice, "create-fail")
        response = self.accepted(result)
        failed = result.execution_handle.result(timeout=3)

        self.assertEqual(failed.state, JobState.FAILED)
        self.assertEqual(failed.failure_code, FailureCategory.PROVIDER)
        self.assertIsNone(failed.instance_id)
        self.assertEqual(self.provider.records, ())
        self.assertNotIn("private provider detail", repr(failed.to_wire()))
        self.assertNotIn("resource_id", failed.to_wire())
        self.assertEqual(self.job_for(response).state, JobState.FAILED)

    def test_partial_create_failure_preserves_known_fixture_instance_id(self):
        provider = PartialCreateProvider()
        tracker = FixtureJobTracker()
        executor = LocalExecutionEngine(tracker)
        service = AsynchronousLifecycleManagementService(
            tracker=tracker,
            executor=executor,
            provider=provider,
        )
        try:
            result = service.handle(create_payload(), self.alice, "partial-create")
            response = self.accepted(result)
            failed = result.execution_handle.result(timeout=3)
            self.assertEqual(failed.state, JobState.FAILED)
            self.assertEqual(failed.failure_code, FailureCategory.PROVIDER)
            self.assertIsNotNone(failed.instance_id)
            self.assertTrue(failed.instance_id.startswith("fixture-instance-"))
            self.assertEqual(len(provider.records), 1)
            self.assertEqual(service.get_job(response.job_id, PROJECT, self.alice).job.instance_id,
                             failed.instance_id)
        finally:
            executor.close()

    def test_stop_start_delete_workflow_updates_fixture_state(self):
        instance = self.provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)

        stopped_result = self.service.handle(
            instance_payload("compute.stop", instance.instance_id), self.alice, "stop-001"
        )
        stopped_response = self.accepted(stopped_result)
        stopped_job = stopped_result.execution_handle.result(timeout=3)
        self.assertEqual(stopped_job.state, JobState.SUCCEEDED)
        self.assertEqual(stopped_job.instance_id, instance.instance_id)
        self.assertEqual(self.provider.inspect(instance.instance_id).state, InstanceState.STOPPED)

        started_result = self.service.handle(
            instance_payload("compute.start", instance.instance_id), self.alice, "start-001"
        )
        started_response = self.accepted(started_result)
        started_job = started_result.execution_handle.result(timeout=3)
        self.assertEqual(started_job.state, JobState.SUCCEEDED)
        self.assertEqual(self.provider.inspect(instance.instance_id).state, InstanceState.ACTIVE)

        deleted_result = self.service.handle(
            instance_payload("compute.delete", instance.instance_id), self.alice, "delete-001"
        )
        deleted_response = self.accepted(deleted_result)
        deleted_job = deleted_result.execution_handle.result(timeout=3)
        self.assertEqual(deleted_job.state, JobState.SUCCEEDED)
        self.assertEqual(deleted_job.instance_id, instance.instance_id)
        tombstone = self.provider.inspect(instance.instance_id)
        self.assertEqual(tombstone.state, InstanceState.DELETED)
        self.assertTrue(all(
            self.job_for(response).state is JobState.SUCCEEDED
            for response in (stopped_response, started_response, deleted_response)
        ))

    def test_instance_inspection_is_synchronous_and_does_not_create_a_job(self):
        instance = self.provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
        result = self.service.handle(
            instance_payload("compute.inspect", instance.instance_id), self.viewer
        )

        self.assertIsNone(result.error)
        self.assertIsNone(result.execution_handle)
        self.assertIsNone(result.job)
        self.assertIsInstance(result.response, LifecycleResult)
        self.assertEqual(result.response.record.instance_id, instance.instance_id)
        self.assertEqual(result.response.record.status, "simulated")
        self.assertEqual(result.response.record.state, InstanceState.ACTIVE)

    def test_cross_project_instance_and_job_access_are_non_disclosing(self):
        instance = self.provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
        created = self.service.handle(create_payload(), self.alice, "scope-create")
        response = self.accepted(created)
        created.execution_handle.result(timeout=3)

        foreign_instance = self.service.handle(
            instance_payload("compute.start", instance.instance_id, "fixture:project-beta"),
            self.mallory,
            "cross-instance",
        )
        self.assertIsNone(foreign_instance.response)
        self.assertEqual(foreign_instance.error.code, ServiceErrorCode.RESOURCE_UNAVAILABLE)

        foreign_job = self.service.get_job(response.job_id, "fixture:project-beta", self.mallory)
        self.assertIsNone(foreign_job.job)
        self.assertEqual(foreign_job.error.code, ServiceErrorCode.RESOURCE_UNAVAILABLE)
        own_job = self.service.get_job(response.job_id, PROJECT, self.alice)
        self.assertEqual(own_job.job.job_id, response.job_id)

    def test_role_authorization_is_required_for_mutations_but_viewer_can_inspect(self):
        instance = self.provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
        denied_create = self.service.handle(create_payload(), self.viewer)
        self.assertEqual(denied_create.error.code, ServiceErrorCode.AUTHORIZATION_DENIED)

        denied_stop = self.service.handle(
            instance_payload("compute.stop", instance.instance_id), self.viewer
        )
        self.assertEqual(denied_stop.error.code, ServiceErrorCode.AUTHORIZATION_DENIED)
        self.assertEqual(self.provider.inspect(instance.instance_id).state, InstanceState.ACTIVE)

        allowed_inspect = self.service.handle(
            instance_payload("compute.inspect", instance.instance_id), self.viewer
        )
        self.assertIsNone(allowed_inspect.error)
        self.assertEqual(allowed_inspect.response.record.instance_id, instance.instance_id)

    def test_concurrent_identical_idempotency_requests_submit_one_operation(self):
        provider = GatedCreateProvider()
        tracker = FixtureJobTracker()
        executor = LocalExecutionEngine(tracker)
        service = AsynchronousLifecycleManagementService(
            tracker=tracker,
            executor=executor,
            provider=provider,
        )
        barrier = threading.Barrier(2)

        def request():
            barrier.wait()
            return service.handle(create_payload(), self.alice, "same-request-key")

        try:
            with ThreadPoolExecutor(max_workers=2) as clients:
                results = list(clients.map(lambda _index: request(), range(2)))
            self.assertTrue(provider.started.wait(timeout=3))
            responses = [self.accepted(result) for result in results]
            self.assertEqual(responses[0].job_id, responses[1].job_id)
            self.assertEqual(provider.calls, 1)
            handles = [result.execution_handle for result in results if result.execution_handle is not None]
            self.assertEqual(len(handles), 1)
            provider.release.set()
            self.assertEqual(handles[0].result(timeout=3).state, JobState.SUCCEEDED)
        finally:
            provider.release.set()
            executor.close()

    def test_conflicting_concurrent_mutation_of_one_instance_is_rejected(self):
        provider = GatedTransitionProvider()
        instance = provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
        tracker = FixtureJobTracker(job_id_factory=iter(("fixture-job-stop", "fixture-job-start")).__next__)
        executor = LocalExecutionEngine(tracker)
        service = AsynchronousLifecycleManagementService(
            tracker=tracker,
            executor=executor,
            provider=provider,
        )
        try:
            stop = service.handle(instance_payload("compute.stop", instance.instance_id), self.alice, "stop-key")
            stop_response = self.accepted(stop)
            self.assertTrue(provider.started.wait(timeout=3))

            start = service.handle(instance_payload("compute.start", instance.instance_id), self.alice, "start-key")
            self.assertIsNone(start.response)
            self.assertEqual(start.error.code, ServiceErrorCode.CONFLICT)
            self.assertEqual(provider.transition_calls, 1)
            rejected_job = service.get_job("fixture-job-start", PROJECT, self.alice).job
            self.assertEqual(rejected_job.state, JobState.FAILED)
            self.assertEqual(rejected_job.failure_code, FailureCategory.CONFLICT)

            provider.release.set()
            self.assertEqual(stop.execution_handle.result(timeout=3).state, JobState.SUCCEEDED)
            self.assertEqual(service.get_job(stop_response.job_id, PROJECT, self.alice).job.state,
                             JobState.SUCCEEDED)
            self.assertEqual(provider.inspect(instance.instance_id).state, InstanceState.STOPPED)
        finally:
            provider.release.set()
            executor.close()

    def test_capacity_exhaustion_returns_error_and_never_accepted(self):
        provider = GatedCreateProvider()
        tracker = FixtureJobTracker(job_id_factory=iter(("fixture-job-capacity-1", "fixture-job-capacity-2")).__next__)
        executor = LocalExecutionEngine(tracker, max_workers=1, max_pending=0)
        service = AsynchronousLifecycleManagementService(
            tracker=tracker,
            executor=executor,
            provider=provider,
        )
        try:
            first = service.handle(create_payload(), self.alice, "capacity-1")
            self.accepted(first)
            self.assertTrue(provider.started.wait(timeout=3))

            second = service.handle(create_payload(), self.alice, "capacity-2")
            self.assertIsNone(second.response)
            self.assertEqual(second.error.code, ServiceErrorCode.SERVICE_UNAVAILABLE)
            rejected = tracker.get("fixture-job-capacity-2", PROJECT, self.alice)
            self.assertEqual(rejected.state, JobState.FAILED)
            self.assertEqual(rejected.failure_code, FailureCategory.EXECUTION_REJECTED)
            replay = service.handle(create_payload(), self.alice, "capacity-2")
            self.assertIsNone(replay.response)
            self.assertEqual(replay.error.code, ServiceErrorCode.SERVICE_UNAVAILABLE)

            provider.release.set()
            self.assertEqual(first.execution_handle.result(timeout=3).state, JobState.SUCCEEDED)
        finally:
            provider.release.set()
            executor.close()

    def test_executor_submission_failure_returns_error_and_failed_job(self):
        class RejectingPool:
            def submit(self, *_args, **_kwargs):
                raise RuntimeError("private executor diagnostic")

            def shutdown(self, wait=True):
                return None

        tracker = FixtureJobTracker(job_id_factory=lambda: "fixture-job-submit-failure")
        with patch("openstack_cloud_poc.execution.ThreadPoolExecutor", return_value=RejectingPool()):
            executor = LocalExecutionEngine(tracker)
        service = AsynchronousLifecycleManagementService(
            tracker=tracker,
            executor=executor,
            provider=self.provider,
        )
        try:
            result = service.handle(create_payload(), self.alice, "submit-failure")
            self.assertIsNone(result.response)
            self.assertEqual(result.error.code, ServiceErrorCode.SERVICE_UNAVAILABLE)
            self.assertNotIn("private executor diagnostic", repr(result.error))
            job = tracker.get("fixture-job-submit-failure", PROJECT, self.alice)
            self.assertEqual(job.state, JobState.FAILED)
            self.assertEqual(job.failure_code, FailureCategory.EXECUTION_REJECTED)
            self.assertEqual(self.provider.records, ())
        finally:
            executor.close()

    def test_worker_authorization_failure_does_not_call_fixture_provider(self):
        tracker = FixtureJobTracker()
        executor = LocalExecutionEngine(tracker, authorizer=lambda _job: False)
        provider = FixtureInstanceProvider()
        service = AsynchronousLifecycleManagementService(
            tracker=tracker,
            executor=executor,
            provider=provider,
        )
        try:
            result = service.handle(create_payload(), self.alice, "worker-denied")
            response = self.accepted(result)
            failed = result.execution_handle.result(timeout=3)
            self.assertEqual(failed.state, JobState.FAILED)
            self.assertEqual(failed.failure_code, FailureCategory.AUTHORIZATION)
            self.assertEqual(provider.records, ())
            self.assertEqual(service.get_job(response.job_id, PROJECT, self.alice).job.state,
                             JobState.FAILED)
        finally:
            executor.close()

    def test_legacy_t13_synchronous_create_remains_validation_only(self):
        service = SynchronousManagementService()
        result = service.handle(create_payload(), self.alice)

        self.assertIsNotNone(result.response)
        self.assertEqual(result.response.status.value, "accepted")
        self.assertEqual(result.response.evidence_mode.value, "simulated")
        self.assertNotIn("job_id", result.response.to_wire())
        self.assertNotIn("resource_id", result.response.to_wire())
        self.assertEqual(self.provider.records, ())

    def test_job_tracker_idempotency_conflict_is_a_safe_service_error(self):
        first = self.service.handle(create_payload(), self.alice, "payload-key")
        response = self.accepted(first)
        conflict_payload = create_payload()
        conflict_payload["network_id"] = "fixture:network.other"
        conflict = self.service.handle(conflict_payload, self.alice, "payload-key")

        self.assertIsNone(conflict.response)
        self.assertEqual(conflict.error.code, ServiceErrorCode.CONFLICT)
        self.assertEqual(self.job_for(response).operation, "compute.create")
        first.execution_handle.result(timeout=3)


if __name__ == "__main__":
    unittest.main()
