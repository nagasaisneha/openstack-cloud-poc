"""Offline tests for the bounded local fixture execution engine."""

from concurrent.futures import ThreadPoolExecutor
import socket
import threading
import unittest
from unittest.mock import patch

from openstack_cloud_poc.api_contract import LifecycleRequest
from openstack_cloud_poc.execution import (
    ExecutionError,
    ExecutionErrorCode,
    LocalExecutionEngine,
)
from openstack_cloud_poc.identity import FixtureIdentityProvider
from openstack_cloud_poc.jobs import (
    FailureCategory,
    FixtureJobTracker,
    JobState,
    JobTrackerError,
)


PROJECT = "fixture:project-alpha"
INSTANCE = "fixture-instance-000019"


def create_request():
    return LifecycleRequest.from_mapping({
        "operation": "compute.create",
        "project_id": PROJECT,
        "image_id": "fixture:image.linux-example",
        "flavor_id": "fixture:flavor.small",
        "network_id": "fixture:network.private-demo",
    })


def existing_instance_request(operation="compute.stop"):
    return LifecycleRequest.from_mapping({
        "operation": operation,
        "project_id": PROJECT,
        "instance_id": INSTANCE,
    })


class ExecutionEngineTests(unittest.TestCase):
    def setUp(self):
        self.tracker = FixtureJobTracker()
        self.alice = FixtureIdentityProvider().authenticate("fixture:alice")

    def register(self, key=None):
        return self.tracker.register(create_request(), self.alice, key).job

    def assert_state(self, job, state):
        current = self.tracker.get(job.job_id, job.project_id, self.alice)
        self.assertEqual(current.state, state)
        return current

    def test_successful_background_execution_and_worker_driven_transitions(self):
        engine = LocalExecutionEngine(self.tracker, max_workers=1, max_pending=1)
        job = self.register()
        started = threading.Event()
        release = threading.Event()
        worker_thread = []

        def operation(running_job):
            worker_thread.append(threading.get_ident())
            self.assertEqual(running_job.state, JobState.RUNNING)
            started.set()
            self.assertTrue(release.wait(timeout=3))
            return INSTANCE

        try:
            handle = engine.submit(job.job_id, operation)
            self.assertFalse(hasattr(handle, "cancel"))
            self.assertTrue(started.wait(timeout=3))
            self.assertNotEqual(worker_thread[0], threading.get_ident())
            self.assert_state(job, JobState.RUNNING)
            release.set()
            result = handle.result(timeout=3)
            self.assertEqual(result.state, JobState.SUCCEEDED)
            self.assertEqual(result.instance_id, INSTANCE)
            self.assertEqual(result.evidence_mode, "simulated")
        finally:
            release.set()
            engine.close()

    def test_execution_rechecks_authorization_before_calling_operation(self):
        called = threading.Event()
        checked = []

        def deny_execution(job):
            checked.append((job.project_id, job.actor_id, job.operation))
            return False

        engine = LocalExecutionEngine(self.tracker, authorizer=deny_execution)
        job = self.register()
        try:
            result = engine.submit(job.job_id, lambda _job: called.set()).result(timeout=3)
            self.assertEqual(checked, [(PROJECT, "fixture:alice", "compute.create")])
            self.assertFalse(called.is_set())
            self.assertEqual(result.state, JobState.FAILED)
            self.assertEqual(result.failure_code, FailureCategory.AUTHORIZATION)
        finally:
            engine.close()

    def test_default_execution_authorizer_uses_stored_fixture_identity(self):
        engine = LocalExecutionEngine(self.tracker)
        job = self.register()
        try:
            result = engine.submit(job.job_id, lambda _job: INSTANCE).result(timeout=3)
            self.assertEqual(result.state, JobState.SUCCEEDED)
            self.assertEqual(result.instance_id, INSTANCE)
        finally:
            engine.close()

    def test_existing_fixture_instance_id_is_preserved_when_callable_returns_none(self):
        engine = LocalExecutionEngine(self.tracker)
        job = self.tracker.register(existing_instance_request(), self.alice).job
        try:
            result = engine.submit(job.job_id, lambda _job: None).result(timeout=3)
            self.assertEqual(result.state, JobState.SUCCEEDED)
            self.assertEqual(result.instance_id, INSTANCE)
        finally:
            engine.close()

    def test_worker_exception_is_sanitized_to_allowlisted_failure(self):
        engine = LocalExecutionEngine(self.tracker)
        job = self.register()

        def fail(_job):
            raise RuntimeError("credential-sentinel exception details")

        try:
            result = engine.submit(job.job_id, fail).result(timeout=3)
            self.assertEqual(result.state, JobState.FAILED)
            self.assertEqual(result.failure_code, FailureCategory.PROVIDER)
            self.assertNotIn("credential-sentinel", repr(result))
            self.assertNotIn("credential-sentinel", repr(result.to_wire()))
        finally:
            engine.close()

    def test_timeout_and_connection_errors_use_safe_failure_categories(self):
        engine = LocalExecutionEngine(self.tracker)
        timeout_job = self.register("timeout")
        connection_job = self.register("connection")
        try:
            timeout = engine.submit(timeout_job.job_id, lambda _job: (_ for _ in ()).throw(TimeoutError()))
            unavailable = engine.submit(connection_job.job_id, lambda _job: (_ for _ in ()).throw(ConnectionError()))
            self.assertEqual(timeout.result(timeout=3).failure_code, FailureCategory.TIMEOUT)
            self.assertEqual(unavailable.result(timeout=3).failure_code, FailureCategory.SERVICE_UNAVAILABLE)
        finally:
            engine.close()

    def test_capacity_exhaustion_fails_without_returning_an_accepted_handle(self):
        engine = LocalExecutionEngine(self.tracker, max_workers=1, max_pending=1)
        running_job = self.register("capacity-running")
        queued_job = self.register("capacity-queued")
        rejected_job = self.register("capacity-rejected")
        started = threading.Event()
        release = threading.Event()

        def blocked(_job):
            started.set()
            self.assertTrue(release.wait(timeout=3))
            return None

        try:
            first = engine.submit(running_job.job_id, blocked)
            self.assertTrue(started.wait(timeout=3))
            second = engine.submit(queued_job.job_id, lambda _job: None)
            with self.assertRaises(ExecutionError) as rejected:
                engine.submit(rejected_job.job_id, lambda _job: None)
            self.assertEqual(rejected.exception.code, ExecutionErrorCode.CAPACITY_EXHAUSTED)
            self.assertEqual(self.assert_state(rejected_job, JobState.FAILED).failure_code,
                             FailureCategory.EXECUTION_REJECTED)
            release.set()
            self.assertEqual(first.result(timeout=3).state, JobState.SUCCEEDED)
            self.assertEqual(second.result(timeout=3).state, JobState.SUCCEEDED)
        finally:
            release.set()
            engine.close()

    def test_pool_submission_failure_is_not_reported_as_accepted(self):
        class RejectingPool:
            def submit(self, *_args, **_kwargs):
                raise RuntimeError("private submission details")

            def shutdown(self, wait=True):
                self.wait = wait

        pool = RejectingPool()
        job = self.register()
        with patch("openstack_cloud_poc.execution.ThreadPoolExecutor", return_value=pool):
            engine = LocalExecutionEngine(self.tracker)
        try:
            with self.assertRaises(ExecutionError) as failed_submission:
                engine.submit(job.job_id, lambda _job: None)
            self.assertEqual(failed_submission.exception.code, ExecutionErrorCode.SUBMISSION_FAILED)
            self.assertNotIn("private submission details", str(failed_submission.exception))
            self.assertEqual(self.assert_state(job, JobState.FAILED).failure_code,
                             FailureCategory.EXECUTION_REJECTED)
        finally:
            engine.close()
        self.assertTrue(pool.wait)

    def test_shutdown_drains_accepted_work_and_rejects_later_submission(self):
        shutdown_entered = threading.Event()
        real_pool = ThreadPoolExecutor(max_workers=1)

        class ObservedPool:
            def submit(self, *args, **kwargs):
                return real_pool.submit(*args, **kwargs)

            def shutdown(self, wait=True):
                shutdown_entered.set()
                real_pool.shutdown(wait=wait)

        with patch("openstack_cloud_poc.execution.ThreadPoolExecutor", return_value=ObservedPool()):
            engine = LocalExecutionEngine(self.tracker)
        accepted_job = self.register("shutdown-accepted")
        rejected_job = self.register("shutdown-rejected")
        started = threading.Event()
        release = threading.Event()

        def blocked(_job):
            started.set()
            self.assertTrue(release.wait(timeout=3))
            return INSTANCE

        handle = engine.submit(accepted_job.job_id, blocked)
        self.assertTrue(started.wait(timeout=3))

        close_done = threading.Event()

        def close_engine():
            engine.close()
            close_done.set()

        closer = threading.Thread(target=close_engine)
        closer.start()
        try:
            # The pool signal occurs after the engine marks itself closed.
            self.assertTrue(shutdown_entered.wait(timeout=3))
            self.assertTrue(engine.is_shutdown)
            self.assertFalse(close_done.is_set())
            with self.assertRaises(ExecutionError) as after_shutdown:
                engine.submit(rejected_job.job_id, lambda _job: None)
            self.assertEqual(after_shutdown.exception.code, ExecutionErrorCode.SHUTDOWN)
            release.set()
            closer.join(timeout=3)
            self.assertFalse(closer.is_alive())
            self.assertTrue(close_done.is_set())
            self.assertEqual(handle.result(timeout=3).state, JobState.SUCCEEDED)
            self.assertEqual(self.assert_state(rejected_job, JobState.FAILED).failure_code,
                             FailureCategory.EXECUTION_REJECTED)
        finally:
            release.set()
            engine.close()
            closer.join(timeout=3)

    def test_concurrent_submissions_obey_total_running_and_pending_bound(self):
        workers = 2
        pending = 2
        submission_count = 10
        engine = LocalExecutionEngine(self.tracker, max_workers=workers, max_pending=pending)
        jobs = [self.register(f"concurrent-{index}") for index in range(submission_count)]
        barrier = threading.Barrier(submission_count)
        workers_started = threading.Event()
        release_workers = threading.Event()
        started_lock = threading.Lock()
        started_count = [0]

        def blocked(_job):
            with started_lock:
                started_count[0] += 1
                if started_count[0] == workers:
                    workers_started.set()
            self.assertTrue(release_workers.wait(timeout=3))
            return None

        def submit_one(job):
            barrier.wait()
            try:
                return engine.submit(job.job_id, blocked)
            except ExecutionError as error:
                return error

        try:
            with ThreadPoolExecutor(max_workers=submission_count) as submitters:
                results = list(submitters.map(submit_one, jobs))
            self.assertTrue(workers_started.wait(timeout=3))
            accepted = [item for item in results if not isinstance(item, ExecutionError)]
            rejected = [item for item in results if isinstance(item, ExecutionError)]
            self.assertEqual(len(accepted), workers + pending)
            self.assertEqual(len(rejected), submission_count - workers - pending)
            self.assertTrue(all(item.code is ExecutionErrorCode.CAPACITY_EXHAUSTED for item in rejected))
            current = [self.tracker.get(job.job_id, job.project_id, self.alice) for job in jobs]
            self.assertEqual(sum(job.state is JobState.RUNNING for job in current), workers)
            self.assertEqual(sum(job.state is JobState.PENDING for job in current), pending)
            self.assertEqual(sum(job.state is JobState.FAILED for job in current), len(rejected))
            release_workers.set()
            for handle in accepted:
                self.assertEqual(handle.result(timeout=3).state, JobState.SUCCEEDED)
        finally:
            release_workers.set()
            engine.close()

    def test_duplicate_submission_for_one_job_is_rejected(self):
        engine = LocalExecutionEngine(self.tracker)
        job = self.register()
        started = threading.Event()
        release = threading.Event()

        def blocked(_job):
            started.set()
            self.assertTrue(release.wait(timeout=3))
            return None

        try:
            first = engine.submit(job.job_id, blocked)
            self.assertTrue(started.wait(timeout=3))
            with self.assertRaises(ExecutionError) as duplicate:
                engine.submit(job.job_id, lambda _job: None)
            self.assertEqual(duplicate.exception.code, ExecutionErrorCode.DUPLICATE_SUBMISSION)
            release.set()
            self.assertEqual(first.result(timeout=3).state, JobState.SUCCEEDED)
        finally:
            release.set()
            engine.close()

    def test_invalid_callable_is_rejected_without_false_acceptance(self):
        engine = LocalExecutionEngine(self.tracker)
        job = self.register()
        try:
            with self.assertRaises(ExecutionError) as invalid:
                engine.submit(job.job_id, None)
            self.assertEqual(invalid.exception.code, ExecutionErrorCode.INVALID_CALLABLE)
            self.assertEqual(self.assert_state(job, JobState.FAILED).failure_code,
                             FailureCategory.VALIDATION)
        finally:
            engine.close()

    def test_execution_engine_makes_no_network_calls(self):
        engine = LocalExecutionEngine(self.tracker)
        job = self.register()
        try:
            with patch.object(socket, "create_connection", side_effect=AssertionError("network call")) as connect:
                result = engine.submit(job.job_id, lambda _job: None).result(timeout=3)
            self.assertEqual(result.state, JobState.SUCCEEDED)
            connect.assert_not_called()
        finally:
            engine.close()

    def test_engine_configuration_is_validated(self):
        for max_workers, max_pending in ((0, 0), (1, -1), (True, 0), (1, True)):
            with self.subTest(max_workers=max_workers, max_pending=max_pending):
                with self.assertRaises(ValueError):
                    LocalExecutionEngine(self.tracker, max_workers, max_pending)


if __name__ == "__main__":
    unittest.main()
