"""Provider state-machine tests are isolated from authenticated facade tests."""
import socket
import sys
import unittest
from unittest.mock import patch

from openstack_cloud_poc.adapter import FixtureManagementAdapter
from openstack_cloud_poc.identity import AuthorizationResult, FixtureIdentityProvider
from openstack_cloud_poc.instance_lifecycle import (
    SOURCE, DuplicateFixtureId, FixtureInstanceLifecycleService, FixtureInstanceProvider,
    InstanceRecord, InstanceState, InvalidTransition, LifecycleErrorCode,
)

PROJECT = "fixture:project-alpha"
IMAGE = "fixture:image.linux-example"
FLAVOR = "fixture:flavor.small"
NETWORK = "fixture:network.private-demo"


def payload():
    return {"operation": "compute.create", "project_id": PROJECT, "image_id": IMAGE,
            "flavor_id": FLAVOR, "network_id": NETWORK}


def next_ids(*values):
    iterator = iter(values)
    return lambda: next(iterator)


class RecordingAdapter(FixtureManagementAdapter):
    def __init__(self):
        super().__init__()
        self.authorizations = []

    def authorize(self, authentication, project_id, operation):
        self.authorizations.append(operation)
        return super().authorize(authentication, project_id, operation)


class PermissiveAdapter(RecordingAdapter):
    """Test-only policy for exercising paths denied by production identity policy."""
    def authorize(self, authentication, project_id, operation):
        self.authorizations.append(operation)
        return AuthorizationResult(True, "simulated", "test-only authorization")


class RecordingProvider(FixtureInstanceProvider):
    def __init__(self, id_generator=None):
        super().__init__(id_generator)
        self.calls = []

    def create(self, project_id, image_id, flavor_id, network_id):
        self.calls.append("create")
        return super().create(project_id, image_id, flavor_id, network_id)

    def inspect(self, instance_id):
        self.calls.append("inspect")
        return super().inspect(instance_id)

    def transition(self, instance_id, target, project_id=None):
        self.calls.append(target.value)
        return super().transition(instance_id, target, project_id)


class ProviderStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.provider = FixtureInstanceProvider()
        self.one = self.provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)

    def test_injected_sequential_ids_and_create_references(self):
        provider = FixtureInstanceProvider(next_ids("fixture-instance-000010", "fixture-instance-000011"))
        first = provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
        second = provider.create(PROJECT, "image-two", "flavor-two", "network-two")
        self.assertEqual((first.instance_id, second.instance_id),
                         ("fixture-instance-000010", "fixture-instance-000011"))
        self.assertEqual((first.image_id, first.flavor_id, first.network_id), (IMAGE, FLAVOR, NETWORK))

    def test_malformed_and_duplicate_generated_ids_leave_state_unchanged(self):
        malformed = FixtureInstanceProvider(next_ids("bad-id"))
        with self.assertRaises(ValueError):
            malformed.create(PROJECT, IMAGE, FLAVOR, NETWORK)
        self.assertEqual(malformed.records, ())

        duplicate = FixtureInstanceProvider(next_ids("fixture-instance-000001", "fixture-instance-000001"))
        duplicate.create(PROJECT, IMAGE, FLAVOR, NETWORK)
        before = duplicate.records
        with self.assertRaises(DuplicateFixtureId):
            duplicate.create(PROJECT, IMAGE, FLAVOR, NETWORK)
        self.assertEqual(duplicate.records, before)

    def test_transition_table_covers_every_approved_state_pair(self):
        cases = (
            (InstanceState.ACTIVE, InstanceState.ACTIVE, InstanceState.ACTIVE),
            (InstanceState.ACTIVE, InstanceState.STOPPED, InstanceState.STOPPED),
            (InstanceState.ACTIVE, InstanceState.DELETED, InstanceState.DELETED),
            (InstanceState.STOPPED, InstanceState.STOPPED, InstanceState.STOPPED),
            (InstanceState.STOPPED, InstanceState.ACTIVE, InstanceState.ACTIVE),
            (InstanceState.STOPPED, InstanceState.DELETED, InstanceState.DELETED),
            (InstanceState.DELETED, InstanceState.DELETED, InstanceState.DELETED),
        )
        for initial, target, expected in cases:
            with self.subTest(initial=initial, target=target):
                provider = FixtureInstanceProvider()
                record = provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
                if initial is not InstanceState.ACTIVE:
                    record = provider.transition(record.instance_id, initial)
                before = provider.records
                result = provider.transition(record.instance_id, target)
                self.assertEqual(result.state, expected)
                if initial is target:
                    self.assertIs(result, record)
                    self.assertEqual(provider.records, before)

        for target in (InstanceState.ACTIVE, InstanceState.STOPPED):
            with self.subTest(initial=InstanceState.DELETED, target=target):
                provider = FixtureInstanceProvider()
                record = provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
                deleted = provider.transition(record.instance_id, InstanceState.DELETED)
                before = provider.records
                with self.assertRaises(InvalidTransition):
                    provider.transition(record.instance_id, target)
                self.assertEqual(provider.records, before)
                self.assertEqual(provider.inspect(record.instance_id), deleted)

    def test_unsupported_transition_target_is_rejected_without_mutation(self):
        before = self.provider.records
        with self.assertRaises(InvalidTransition):
            self.provider.transition(self.one.instance_id, "active")
        self.assertEqual(self.provider.records, before)

    def test_seeded_records_are_inspect_only_and_ids_validate(self):
        seed = InstanceRecord("fixture-instance-000099", PROJECT, InstanceState.STOPPED)
        self.provider.seed(seed)
        stored = self.provider.inspect(seed.instance_id)
        self.assertFalse(stored.provider_created)
        before = self.provider.records
        with self.assertRaises(PermissionError):
            self.provider.transition(seed.instance_id, InstanceState.ACTIVE)
        self.assertEqual(self.provider.records, before)
        for value in ("other-1", "fixture-instance-", "fixture-instance-abc", "fixture-instance-１２"):
            with self.assertRaises(ValueError):
                self.provider.inspect(value)
        with self.assertRaises(ValueError):
            self.provider.seed(seed)

        for bad_seed in (
            InstanceRecord("fixture-instance-000098", PROJECT, "active"),
            InstanceRecord("fixture-instance-000097", " \n", InstanceState.ACTIVE),
        ):
            with self.subTest(seed=bad_seed):
                with self.assertRaises(ValueError):
                    self.provider.seed(bad_seed)

    def test_create_and_transition_failures_are_atomic(self):
        for failure in (TimeoutError("private timeout"), RuntimeError("private provider detail")):
            provider = FixtureInstanceProvider(next_ids("fixture-instance-000001"))
            provider.fail_next = failure
            with self.assertRaises(type(failure)):
                provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
            self.assertEqual(provider.records, ())

            provider = FixtureInstanceProvider()
            record = provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
            before = provider.records
            provider.fail_next = failure
            with self.assertRaises(type(failure)):
                provider.transition(record.instance_id, InstanceState.STOPPED)
            self.assertEqual(provider.records, before)


class AuthenticatedServiceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = RecordingAdapter()
        self.provider = RecordingProvider()
        self.service = FixtureInstanceLifecycleService(self.adapter, self.provider)
        identities = FixtureIdentityProvider()
        self.alice = identities.authenticate("fixture:alice")
        self.bob = identities.authenticate("fixture:bob")

    def assert_simulated(self, value):
        self.assertEqual(value.status, "simulated")
        self.assertIn("fixture:", value.source)

    def test_create_preserves_validated_references_and_simulated_provenance(self):
        result = self.service.create(payload(), self.alice)
        self.assertIsNotNone(result.record)
        self.assertEqual((result.record.image_id, result.record.flavor_id, result.record.network_id),
                         (IMAGE, FLAVOR, NETWORK))
        for item in (result, result.record):
            self.assert_simulated(item)
        self.assertNotIn("resource_id", repr(result))
        self.assertNotIn("job_id", repr(result))

    def test_safe_create_validation_operation_and_fixture_failures(self):
        self.assert_error(self.service.create([], self.alice), LifecycleErrorCode.INVALID_REQUEST,
                          "The lifecycle request is invalid.")
        malformed = payload(); malformed.pop("image_id")
        result = self.service.create(malformed, self.alice)
        self.assert_error(result, LifecycleErrorCode.VALIDATION_FAILURE,
                          "The create request did not pass local contract validation.")

        wrong = payload(); wrong["operation"] = "nova.delete-sensitive"
        result = self.service.create(wrong, self.alice)
        self.assert_error(result, LifecycleErrorCode.INVALID_REQUEST,
                          "The lifecycle request is invalid.")
        self.assertNotIn("delete-sensitive", repr(result))
        malformed_wrong = dict(wrong); malformed_wrong.pop("image_id")
        result = self.service.create(malformed_wrong, self.alice)
        self.assert_error(result, LifecycleErrorCode.VALIDATION_FAILURE,
                          "The create request did not pass local contract validation.")

        missing = payload(); missing["network_id"] = "secret-missing-network"
        result = self.service.create(missing, self.alice)
        self.assert_error(result, LifecycleErrorCode.MISSING_FIXTURE_DATA,
                          "A referenced fixture record is missing.")
        self.assertNotIn("secret-missing-network", repr(result))

    def test_identity_without_compute_role_is_denied_before_provider_access(self):
        for method in ("inspect", "start", "stop", "delete"):
            result = getattr(self.service, method)("fixture-instance-000001", PROJECT, self.bob)
            self.assert_error(result, LifecycleErrorCode.AUTHORIZATION_DENIED,
                              "Project authorization was denied.")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.adapter.authorizations,
                         ["compute.inspect", "compute.start", "compute.stop", "compute.delete"])

    def test_create_requires_authentication_and_create_role(self):
        self.assert_error(self.service.create(payload(), None), LifecycleErrorCode.AUTHORIZATION_DENIED,
                          "Project authorization was denied.")
        self.assert_error(self.service.create(payload(), self.bob), LifecycleErrorCode.AUTHORIZATION_DENIED,
                          "Project authorization was denied.")
        self.assertEqual(self.provider.calls, [])

    def test_permissive_service_scope_seed_protection_and_tombstones(self):
        provider = RecordingProvider()
        seed = InstanceRecord("fixture-instance-000099", PROJECT, InstanceState.STOPPED)
        provider.seed(seed)
        adapter = PermissiveAdapter()
        service = FixtureInstanceLifecycleService(adapter, provider)
        inspected = service.inspect(seed.instance_id, PROJECT, self.alice)
        self.assertEqual(inspected.record.state, InstanceState.STOPPED)
        before = provider.records
        denied = service.start(seed.instance_id, PROJECT, self.alice)
        self.assert_error(denied, LifecycleErrorCode.INSTANCE_NOT_TASK_CREATED,
                          "The fixture instance was not created by this lifecycle provider.")
        self.assertEqual(provider.records, before)

        created = provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
        outside = service.inspect(created.instance_id, "fixture:project-beta", self.alice)
        same = service.inspect("fixture-instance-000777", "fixture:project-beta", self.alice)
        self.assertEqual(outside.error, same.error)
        self.assert_error(outside, LifecycleErrorCode.INSTANCE_NOT_FOUND,
                          "No matching fixture instance exists.")
        self.assertNotIn(created.instance_id, repr(outside))

        tombstone = service.delete(created.instance_id, PROJECT, self.alice)
        self.assertEqual(tombstone.record.state, InstanceState.DELETED)
        after_delete = provider.records
        for method in ("start", "stop"):
            invalid = getattr(service, method)(created.instance_id, PROJECT, self.alice)
            self.assert_error(invalid, LifecycleErrorCode.INVALID_TRANSITION,
                              "The requested lifecycle transition is invalid.")
            self.assertEqual(provider.records, after_delete)
        visible = service.inspect(created.instance_id, PROJECT, self.alice)
        self.assertEqual(visible.record.state, InstanceState.DELETED)
        self.assert_simulated(tombstone.record)

    def test_permissive_service_failure_codes_are_safe_and_state_atomic(self):
        for failure, code, message in (
            (TimeoutError("secret timeout"), LifecycleErrorCode.TIMEOUT, "The fixture lifecycle operation timed out."),
            (RuntimeError("secret exception input"), LifecycleErrorCode.PROVIDER_FAILURE, "The fixture lifecycle provider failed."),
        ):
            provider = RecordingProvider(next_ids("fixture-instance-000001"))
            provider.fail_next = failure
            service = FixtureInstanceLifecycleService(PermissiveAdapter(), provider)
            result = service.create(payload(), self.alice)
            self.assert_error(result, code, message)
            self.assertNotIn(str(failure), repr(result))
            self.assertEqual(provider.records, ())

            transition_provider = RecordingProvider()
            record = transition_provider.create(PROJECT, IMAGE, FLAVOR, NETWORK)
            before = transition_provider.records
            transition_provider.fail_next = failure
            transition_service = FixtureInstanceLifecycleService(PermissiveAdapter(), transition_provider)
            transition = transition_service.stop(record.instance_id, PROJECT, self.alice)
            self.assert_error(transition, code, message)
            self.assertNotIn(str(failure), repr(transition))
            self.assertEqual(transition_provider.records, before)

    def test_invalid_and_duplicate_ids_map_to_safe_errors(self):
        provider = RecordingProvider(next_ids("malformed"))
        service = FixtureInstanceLifecycleService(PermissiveAdapter(), provider)
        result = service.create(payload(), self.alice)
        self.assert_error(result, LifecycleErrorCode.INVALID_FIXTURE_ID,
                          "The fixture instance identifier is invalid.")
        self.assertEqual(provider.records, ())

        provider = RecordingProvider(next_ids("fixture-instance-000001", "fixture-instance-000001"))
        service = FixtureInstanceLifecycleService(PermissiveAdapter(), provider)
        first = service.create(payload(), self.alice)
        before = provider.records
        duplicate = service.create(payload(), self.alice)
        self.assertEqual(first.record.instance_id, "fixture-instance-000001")
        self.assert_error(duplicate, LifecycleErrorCode.DUPLICATE_FIXTURE_ID,
                          "The fixture instance identifier already exists.")
        self.assertEqual(provider.records, before)

    def test_no_network_endpoint_or_openstack_client_dependencies(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("endpoint sentinel")) as connect:
            self.service.create(payload(), self.alice)
        connect.assert_not_called()
        for name in ("openstack", "novaclient", "keystoneauth1", "proxmox"):
            self.assertNotIn(name, sys.modules)

    def assert_error(self, result, code, message):
        self.assertIsNone(result.record)
        self.assertEqual(result.error.code, code)
        self.assertEqual(result.error.message, message)
        self.assertEqual(result.error.status, "simulated")
        self.assertEqual(result.error.source, SOURCE)
        self.assertEqual(result.status, "simulated")
        self.assertEqual(result.source, SOURCE)


if __name__ == "__main__":
    unittest.main()
