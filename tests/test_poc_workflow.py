"""Repeatable end-to-end tests for the local fixture POC workflow."""

import socket
import unittest
from unittest.mock import patch

from openstack_cloud_poc.adapter import FixtureManagementAdapter
from openstack_cloud_poc.api_contract import LifecycleAcceptedResponse
from openstack_cloud_poc.catalog import CatalogCategory
from openstack_cloud_poc.execution import LocalExecutionEngine
from openstack_cloud_poc.identity import FixtureIdentityProvider
from openstack_cloud_poc.instance_lifecycle import FixtureInstanceProvider, InstanceState
from openstack_cloud_poc.jobs import FixtureJobTracker, JobState
from openstack_cloud_poc.management_service import AsynchronousLifecycleManagementService


PROJECT = "fixture:project-alpha"


class FixturePocWorkflowTests(unittest.TestCase):
    def test_two_repeatable_create_inspect_stop_start_delete_runs(self):
        adapter = FixtureManagementAdapter()
        identities = FixtureIdentityProvider()
        alice = identities.authenticate("fixture:alice")
        provider = FixtureInstanceProvider()
        tracker = FixtureJobTracker()
        executor = LocalExecutionEngine(tracker)
        service = AsynchronousLifecycleManagementService(
            adapter=adapter,
            tracker=tracker,
            executor=executor,
            provider=provider,
        )

        try:
            catalog_result = adapter.read_catalog(alice, PROJECT)
            self.assertTrue(catalog_result.authorization.allowed)
            self.assertIsNotNone(catalog_result.catalog)
            image = catalog_result.catalog.records_for(CatalogCategory.IMAGE)[0]
            flavor = catalog_result.catalog.records_for(CatalogCategory.FLAVOR)[0]
            network = catalog_result.catalog.records_for(CatalogCategory.NETWORK)[0]

            created_ids = []
            job_ids = []
            with patch.object(socket, "create_connection", side_effect=AssertionError("network call")):
                for run in range(2):
                    created = service.handle(
                        {
                            "operation": "compute.create",
                            "project_id": PROJECT,
                            "image_id": image.identifier,
                            "flavor_id": flavor.identifier,
                            "network_id": network.identifier,
                        },
                        alice,
                        f"t17-create-{run}",
                    )
                    self.assertIsNone(created.error)
                    self.assertIsInstance(created.response, LifecycleAcceptedResponse)
                    self.assertEqual(created.response.evidence_mode.value, "simulated")
                    self.assertNotIn("instance_id", created.response.to_wire())
                    self.assertNotIn("resource_id", created.response.to_wire())
                    created_job = created.execution_handle.result(timeout=3)
                    self.assertEqual(created_job.state, JobState.SUCCEEDED)
                    self.assertIsNotNone(created_job.instance_id)
                    created_ids.append(created_job.instance_id)
                    job_ids.append(created.response.job_id)

                    inspected = service.handle(
                        {
                            "operation": "compute.inspect",
                            "project_id": PROJECT,
                            "instance_id": created_job.instance_id,
                        },
                        alice,
                    )
                    self.assertIsNone(inspected.error)
                    self.assertIsNone(inspected.execution_handle)
                    self.assertEqual(inspected.response.record.state, InstanceState.ACTIVE)

                    for operation, expected in (
                        ("compute.stop", InstanceState.STOPPED),
                        ("compute.start", InstanceState.ACTIVE),
                        ("compute.delete", InstanceState.DELETED),
                    ):
                        result = service.handle(
                            {
                                "operation": operation,
                                "project_id": PROJECT,
                                "instance_id": created_job.instance_id,
                            },
                            alice,
                            f"t17-{operation.rsplit('.', 1)[1]}-{run}",
                        )
                        self.assertIsNone(result.error)
                        self.assertIsInstance(result.response, LifecycleAcceptedResponse)
                        final_job = result.execution_handle.result(timeout=3)
                        self.assertEqual(final_job.state, JobState.SUCCEEDED)
                        inspected = service.handle(
                            {
                                "operation": "compute.inspect",
                                "project_id": PROJECT,
                                "instance_id": created_job.instance_id,
                            },
                            alice,
                        )
                        self.assertEqual(inspected.response.record.state, expected)

            self.assertEqual(len(set(created_ids)), 2)
            self.assertEqual(len(set(job_ids)), 2)
            self.assertEqual(len(provider.records), 2)
            self.assertTrue(all(record.status == "simulated" for record in provider.records))
        finally:
            executor.close()


if __name__ == "__main__":
    unittest.main()
