"""Behavioral tests with real Jumpstarter streams and a stateful Kubernetes API."""

import asyncio  # noqa: TID251
import gzip
import json
import logging
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from click.testing import CliRunner
from lightkube.core.exceptions import ApiError
from lightkube.models.meta_v1 import Status
from lightkube.resources.core_v1 import PersistentVolumeClaim
from lightkube.resources.storage_v1 import StorageClass

from .driver import KubevirtPower, KubevirtStorage
from .storage import Disk, Flash, disk_names, volumes
from .vm import (
    FLASH_ANNOTATION,
    DataVolume,
    UploadTokenRequest,
    VirtualMachine,
    VirtualMachineInstance,
    VMControl,
    operation,
)
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve
from jumpstarter.streams.encoding import Compression


def api_error(code):
    return ApiError(status=Status(code=code, message=f"API error {code}"))


class Cluster:
    """Models API copies, optimistic concurrency, CDI readiness, and delayed shutdown."""

    def __init__(self):
        self.objects = {}
        self.uploads = {}
        self.deleted = []
        self.activations = []
        self.fail_upload = None
        self.fail_delete = False
        self.lose_commit_response = False
        self.complete_uploads = True
        self.stop_after = 0
        self.patch_count = 0
        self.race_patch = None
        self.vmi = None
        self.before_patch = None
        self.pvc_linger = 0
        self.put(StorageClass, "fast", {"volumeBindingMode": "Immediate", "provisioner": "test.csi.example"})
        self.put(
            VirtualMachine,
            "vm",
            {
                "spec": {
                    "runStrategy": "Always",
                    "template": {
                        "spec": {
                            "volumes": [
                                {"name": n, "dataVolume": {"name": f"vm-{n}"}} for n in ("root", "data", "logs")
                            ]
                            + [{"name": "cloudinit", "cloudInitNoCloud": {"userData": "keep me"}}],
                            "domain": {
                                "devices": {
                                    "disks": [
                                        {"name": n, "serial": n, "disk": {"bus": "virtio"}}
                                        for n in ("root", "data", "logs")
                                    ]
                                }
                            },
                        }
                    },
                },
            },
        )
        for name in ("root", "data", "logs"):
            self.put(
                DataVolume,
                f"vm-{name}",
                {
                    "metadata": {
                        "ownerReferences": [
                            {
                                "apiVersion": "kubevirt.io/v1",
                                "kind": "VirtualMachine",
                                "name": "vm",
                                "uid": "vm-uid",
                            }
                        ]
                    },
                    "spec": {"source": {"blank": {}}, "storage": {"resources": {"requests": {"storage": "1Gi"}}}},
                    "status": {"phase": "Succeeded"},
                },
            )
            self.put(
                PersistentVolumeClaim,
                f"vm-{name}",
                {
                    "spec": {"storageClassName": "fast", "volumeMode": "Block"},
                },
            )

    def put(self, kind, name, obj):
        obj = deepcopy(obj)
        obj.setdefault("metadata", {}).update(name=name, uid=f"{name}-uid", resourceVersion="1")
        self.objects[kind, name] = obj

    @property
    def vm(self):
        return VirtualMachine.from_dict(deepcopy(self.objects[VirtualMachine, "vm"]))

    async def get(self, kind, name, **kwargs):
        if kind is VirtualMachineInstance:
            if self.vm["spec"]["runStrategy"] == "Halted":
                if self.stop_after == 0:
                    self.vmi = None
                else:
                    self.stop_after -= 1
            if self.vmi is None:
                raise api_error(404)
            return VirtualMachineInstance.from_dict(deepcopy(self.vmi))
        if (kind, name) not in self.objects:
            raise api_error(404)
        return kind.from_dict(deepcopy(self.objects[kind, name]))

    async def create(self, obj):
        if isinstance(obj, UploadTokenRequest):
            obj["status"] = {"token": obj["spec"]["pvcName"]}
            return obj
        name = obj.metadata.name
        if (type(obj), name) in self.objects:
            raise api_error(409)
        self.put(type(obj), name, obj.to_dict())
        if isinstance(obj, DataVolume):
            self.objects[DataVolume, name]["status"] = {"phase": "UploadReady"}
        return await self.get(type(obj), name)

    def _race(self, kind, name):
        if self.race_patch == self.patch_count:
            # virt-controller rewrote VM status between this client's read and write.
            current = self.objects[kind, name]
            current["metadata"]["resourceVersion"] = str(int(current["metadata"]["resourceVersion"]) + 1)

    @staticmethod
    def _fence(current, obj):
        # A patch without metadata is unconditional, as an API server merge patch is.
        patch_metadata = obj.get("metadata", {})
        for field in ("resourceVersion", "uid"):
            if patch_metadata.get(field, current["metadata"][field]) != current["metadata"][field]:
                raise api_error(409)

    async def patch(self, kind, name, obj, **kwargs):
        self.patch_count += 1
        if self.before_patch:
            callback, self.before_patch = self.before_patch, None
            await callback()
        self._race(kind, name)
        current = self.objects[kind, name]
        self._fence(current, obj)

        def merge(target, patch):
            for key, value in patch.items():
                if isinstance(value, dict):
                    merge(target.setdefault(key, {}), value)
                elif value is None:
                    target.pop(key, None)
                else:
                    target[key] = deepcopy(value)

        previous = deepcopy(current)
        merge(current, obj)
        current["metadata"]["resourceVersion"] = str(int(current["metadata"]["resourceVersion"]) + 1)
        if volumes(previous) != volumes(current):
            self.activations.append(deepcopy(current))
            if self.lose_commit_response:
                self.lose_commit_response = False
                raise ConnectionError("response lost after API server committed update")
        return await self.get(kind, name)

    async def delete(self, kind, name, **kwargs):
        if self.fail_delete:
            raise api_error(500)
        if (kind, name) not in self.objects:
            raise api_error(404)
        self.deleted.append(name)
        del self.objects[kind, name]
        if kind is DataVolume and (PersistentVolumeClaim, name) in self.objects:
            # CDI releases the claim with its DataVolume; a terminating upload pod
            # holds it for its grace period after that.

            async def release_claim():
                await asyncio.sleep(self.pvc_linger)
                self.objects.pop((PersistentVolumeClaim, name), None)

            asyncio.get_running_loop().create_task(release_claim())

    async def upload(self, stream, token):
        status = self.objects[DataVolume, token].get("status", {})
        if status.get("phase") != "UploadReady":
            # CDI only serves an upload once the volume and its pod are ready.
            raise RuntimeError(f"no upload server for {token}: DataVolume is {status.get('phase')}")
        chunks = [chunk async for chunk in stream]
        data = b"".join(chunks)
        self.uploads[token] = data
        if data == self.fail_upload:
            # CDI's upload server exits after a failed conversion or resize, the pod
            # restarts, and the DataVolume returns to UploadReady instead of Failed.
            self.objects[DataVolume, token]["status"] = {
                "phase": "UploadReady",
                "restartCount": (status.get("restartCount") or 0) + 1,
                "conditions": [
                    {"type": "Running", "status": "False", "reason": "Failed", "message": "conversion failed"}
                ],
            }
            return
        # An in-flight upload keeps the volume at UploadReady; there is no separate phase.
        self.objects[DataVolume, token]["status"] = {"phase": "Succeeded" if self.complete_uploads else "UploadReady"}


@pytest.fixture
def cluster(monkeypatch):
    cluster = Cluster()

    async def upload(_self, stream, token):
        await cluster.upload(stream, token)

    monkeypatch.setattr(Flash, "_upload", upload)
    return cluster


def storage(cluster, **kwargs):
    config: dict[str, Any] = {
        "namespace": "ns",
        "vm_name": "vm",
        "upload_proxy_url": "https://upload.example.test",
        "disks": {
            n: Disk(size="1Gi", storage_class_name="fast", volume_mode="Block") for n in ("root", "data", "logs")
        },
        "poll_interval": 0.001,
        "shutdown_timeout": 0.1,
    }
    config.update(kwargs)
    driver = KubevirtStorage(**config)
    driver._client = cluster
    return driver


def power(cluster):
    driver = KubevirtPower(namespace="ns", vm_name="vm")
    driver._client = cluster
    return driver


class Bytes:
    def __init__(self, data):
        self.data = data

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.data:
            raise StopAsyncIteration
        chunk, self.data = self.data[:7], self.data[7:]
        return chunk


@asynccontextmanager
async def image(data=b"image"):
    yield Bytes(data)


async def begin(driver, targets):
    operation_id = uuid4().hex
    await driver.begin_flash(operation_id, targets)
    return operation_id


async def stage(driver, operation_id, target, data=b"image"):
    await driver._flash().stage(operation_id, target, lambda: image(data))


def test_cli_flashes_two_local_files_over_real_jumpstarter_streams(cluster, tmp_path):
    root, data = tmp_path / "os.raw", tmp_path / "data.raw"
    root.write_bytes(b"boot image" * 20000)
    data.write_bytes(b"data image" * 15000)
    devices = deepcopy(cluster.vm["spec"]["template"]["spec"]["domain"])
    with serve(storage(cluster)) as client:
        result = CliRunner().invoke(client.cli(), ["flash", "-t", f"root:{root}", "-t", f"data:{data}"])
        assert result.exit_code == 0, result.output
    current = disk_names(volumes(cluster.vm))
    assert cluster.uploads[current["root"]] == root.read_bytes()
    assert cluster.uploads[current["data"]] == data.read_bytes()
    assert current["logs"] == "vm-logs"
    assert cluster.vm["spec"]["template"]["spec"]["domain"] == devices
    assert volumes(cluster.vm)[-1]["cloudInitNoCloud"]["userData"] == "keep me"
    assert len(cluster.activations) == 1
    assert cluster.vm["spec"]["runStrategy"] == "Halted"
    assert set(cluster.deleted) == {"vm-root", "vm-data"}


def test_second_disk_conversion_failure_preserves_running_vm_and_all_disks(cluster, tmp_path):
    root, data = tmp_path / "os.raw", tmp_path / "bad.qcow2"
    root.write_bytes(b"boot image")
    data.write_bytes(b"invalid image")
    cluster.fail_upload = data.read_bytes()
    before = deepcopy(cluster.vm["spec"])
    failure = pytest.RaisesExc(DriverError, match="conversion")
    with serve(storage(cluster, flash_timeout=2)) as client, pytest.RaisesGroup(failure):
        client.flash({"root": root, "data": data})
    assert cluster.vm["spec"] == before
    assert not cluster.activations
    assert operation(cluster.vm)["phase"] == "aborted"
    assert {name for kind, name in cluster.objects if kind is DataVolume} == {"vm-root", "vm-data", "vm-logs"}


def test_compression_with_an_http_image_is_rejected_before_begin(cluster):
    with serve(storage(cluster)) as client, pytest.raises(ValueError, match="--compression is not supported"):
        client.flash("https://images.example.test/os.raw.gz", compression=Compression.GZIP)
    assert operation(cluster.vm) is None


def test_a_successful_flash_reports_progress(cluster, tmp_path, caplog):
    root, data = tmp_path / "os.raw", tmp_path / "data.raw"
    root.write_bytes(b"boot image")
    data.write_bytes(b"data image")
    with serve(storage(cluster)) as client, caplog.at_level(logging.INFO):
        client.flash({"root": root, "data": data})
    steps = [
        message
        for message in (record.getMessage() for record in caplog.records)
        if message.startswith(("Preparing ", "Uploading disk '", "Stopping the VM", "Flash complete"))
    ]
    assert steps == [
        "Preparing 2 disk(s): root, data",
        "Uploading disk 'root' (1/2); this includes CDI conversion",
        "Uploading disk 'data' (2/2); this includes CDI conversion",
        "Stopping the VM and switching to the new disk(s)",
        "Flash complete; the VM is off until you power it on",
    ]


@pytest.mark.parametrize(
    "args", [[], ["one", "two"], ["one", "-t", "data:two"], ["-t", "root:a", "-t", "root:b"], ["-t", "data:"]]
)
def test_cli_rejects_ambiguous_inputs_before_changing_cluster(cluster, args):
    before = deepcopy(cluster.objects)
    with serve(storage(cluster)) as client:
        result = CliRunner().invoke(client.cli(), ["flash", *args])
    assert result.exit_code == 2
    assert cluster.objects == before


def test_a_missing_image_is_rejected_before_begin(cluster, tmp_path):
    root = tmp_path / "os.raw"
    root.write_bytes(b"boot image")
    with serve(storage(cluster)) as client, pytest.raises(ValueError, match="cannot read image"):
        client.flash({"root": root, "data": tmp_path / "missing.raw"})
    assert operation(cluster.vm) is None


@pytest.mark.anyio
async def test_commit_requires_every_disk_to_finish(cluster):
    driver = storage(cluster)
    op = await begin(driver, ["root", "data"])
    await stage(driver, op, "root")
    with pytest.raises(RuntimeError, match="Every disk must finish uploading"):
        await driver.commit_flash(op)
    assert cluster.vm["spec"]["runStrategy"] == "Always"
    assert not cluster.activations


@pytest.mark.anyio
async def test_power_stays_available_while_a_disk_is_uploading(cluster):
    driver = storage(cluster)
    cluster.complete_uploads = False
    op = await begin(driver, ["root"])
    task = asyncio.create_task(stage(driver, op, "root"))  # noqa: TID251
    while not cluster.uploads:
        await asyncio.sleep(0)
    assert not task.done()
    # Only the halt-to-swap window is protected; a plain upload does not block power.
    await power(cluster).on()
    assert cluster.vm["spec"]["runStrategy"] == "Always"
    name = next(iter(cluster.uploads))
    cluster.objects[DataVolume, name]["status"]["phase"] = "Succeeded"
    await task
    await driver.commit_flash(op)
    assert disk_names(volumes(cluster.vm))["root"] == name


@pytest.mark.anyio
async def test_cancelled_upload_recovers_after_exporter_restart(cluster, monkeypatch):
    entered = asyncio.Event()

    async def blocked_upload(_self, stream, token):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Flash, "_upload", blocked_upload)
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    task = asyncio.create_task(stage(driver, op, "root"))  # noqa: TID251
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # An exporter restart builds a fresh driver whose session start reclaims the upload.
    fresh = storage(cluster)
    fresh.reset()
    assert await asyncio.wait_for(fresh._reclaim, 2) is False  # noqa: TID251
    assert disk_names(volumes(cluster.vm))["root"] == "vm-root"
    assert operation(cluster.vm)["phase"] == "aborted"
    await power(cluster).on()


def test_lost_commit_response_recovers_success_without_deleting_new_disks(cluster, tmp_path):
    image_path = tmp_path / "os.raw"
    image_path.write_bytes(b"boot image")
    cluster.lose_commit_response = True
    with serve(storage(cluster)) as client:
        client.flash(image_path)
    name = disk_names(volumes(cluster.vm))["root"]
    assert cluster.uploads[name] == image_path.read_bytes()
    assert (DataVolume, name) in cluster.objects
    assert cluster.deleted == ["vm-root"]
    assert operation(cluster.vm)["phase"] == "complete"


@pytest.mark.anyio
async def test_cleanup_failure_is_retryable_without_rolling_back_or_blocking_power(cluster):
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    cluster.fail_delete = True
    await driver.commit_flash(op)
    assert operation(cluster.vm)["phase"] == "committed"
    active = disk_names(volumes(cluster.vm))["root"]
    await power(cluster).on()
    cluster.fail_delete = False
    fresh = storage(cluster)
    fresh.reset()
    assert await asyncio.wait_for(fresh._reclaim, 2) is True  # noqa: TID251
    assert disk_names(volumes(cluster.vm))["root"] == active
    assert (DataVolume, active) in cluster.objects
    assert operation(cluster.vm)["phase"] == "complete"


@pytest.mark.anyio
async def test_reclaim_fences_a_commit_waiting_for_shutdown(cluster):
    driver = storage(cluster, shutdown_timeout=2)
    cluster.vmi = {"metadata": {"name": "vm"}, "spec": {"volumes": deepcopy(volumes(cluster.vm))}}
    cluster.stop_after = 10000
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    task = asyncio.create_task(driver.commit_flash(op))  # noqa: TID251
    while cluster.vm["spec"]["runStrategy"] != "Halted":
        await asyncio.sleep(0)
    fresh = storage(cluster)
    fresh.reset()
    await asyncio.wait_for(fresh._reclaim, 2)  # noqa: TID251
    cluster.stop_after = 0
    with pytest.raises(RuntimeError, match="no longer active"):
        await task
    assert not cluster.activations
    assert disk_names(volumes(cluster.vm))["root"] == "vm-root"


@pytest.mark.anyio
async def test_shutdown_timeout_preserves_disk_references(cluster):
    driver = storage(cluster, shutdown_timeout=0.01)
    cluster.vmi = {"metadata": {"name": "vm"}, "spec": {"volumes": deepcopy(volumes(cluster.vm))}}
    cluster.stop_after = 10000
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    with pytest.raises(TimeoutError):
        await driver.commit_flash(op)
    fresh = storage(cluster)
    fresh.reset()
    await asyncio.wait_for(fresh._reclaim, 2)  # noqa: TID251
    assert not cluster.activations
    assert disk_names(volumes(cluster.vm))["root"] == "vm-root"


@pytest.mark.anyio
async def test_commit_waits_out_the_guests_termination_grace_period(cluster):
    driver = storage(cluster, shutdown_timeout=0.05, poll_interval=0.01)
    cluster.vmi = {
        "metadata": {"name": "vm"},
        "spec": {"volumes": deepcopy(volumes(cluster.vm)), "terminationGracePeriodSeconds": 1},
    }
    # The VMI outlives shutdown_timeout, but not the grace period plus margin.
    cluster.stop_after = 10
    op = await begin(driver, ["root"])
    new = operation(cluster.vm)["disks"]["root"]["new"]
    await stage(driver, op, "root")
    await driver.commit_flash(op)
    assert disk_names(volumes(cluster.vm))["root"] == new
    assert cluster.activations


@pytest.mark.anyio
async def test_power_on_waits_for_the_activation_swap(cluster):
    driver = storage(cluster, shutdown_timeout=2)
    cluster.vmi = {"metadata": {"name": "vm"}, "spec": {"volumes": deepcopy(volumes(cluster.vm))}}
    cluster.stop_after = 10000
    op = await begin(driver, ["root"])
    new = operation(cluster.vm)["disks"]["root"]["new"]
    await stage(driver, op, "root")
    committing = asyncio.create_task(driver.commit_flash(op))  # noqa: TID251
    while cluster.vm["spec"]["runStrategy"] != "Halted":
        await asyncio.sleep(0)
    powering = asyncio.create_task(power(cluster).on())  # noqa: TID251
    # Power waits behind the activation instead of booting the disks it replaces.
    for _ in range(20):
        await asyncio.sleep(0.001)
        assert cluster.vm["spec"]["runStrategy"] == "Halted"
        assert not powering.done()
    cluster.stop_after = 0
    await committing
    await powering
    assert cluster.vm["spec"]["runStrategy"] == "Always"
    assert disk_names(volumes(cluster.vm))["root"] == new


@pytest.mark.anyio
async def test_activation_retries_when_a_status_write_lands_before_the_swap(cluster):
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    new = operation(cluster.vm)["disks"]["root"]["new"]
    await stage(driver, op, "root")
    # Patch 1 is the halt and patch 2 the swap; bump the version right before the swap.
    cluster.race_patch = cluster.patch_count + 2
    await driver.commit_flash(op)
    assert disk_names(volumes(cluster.vm))["root"] == new
    assert operation(cluster.vm)["phase"] == "complete"
    assert cluster.deleted == ["vm-root"]


@pytest.mark.anyio
async def test_recovery_retries_a_conflicting_completion_patch(cluster):
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    cluster.fail_delete = True
    await driver.commit_flash(op)
    cluster.fail_delete = False
    cluster.race_patch = cluster.patch_count + 1
    fresh = storage(cluster)
    fresh.reset()
    assert await asyncio.wait_for(fresh._reclaim, 2) is True  # noqa: TID251
    assert operation(cluster.vm)["phase"] == "complete"
    assert cluster.deleted == ["vm-root"]


@pytest.mark.anyio
async def test_named_disks_preserve_unselected_templates_and_external_volumes(cluster):
    vm = cluster.objects[VirtualMachine, "vm"]
    vm["spec"]["dataVolumeTemplates"] = [
        {"metadata": {"name": name}, "spec": {"source": {"blank": {}}}} for name in ("vm-root", "vm-data")
    ]
    cluster.objects[DataVolume, "vm-root"]["metadata"]["ownerReferences"] = []
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    await driver.commit_flash(op)
    assert [t["metadata"]["name"] for t in cluster.vm["spec"]["dataVolumeTemplates"]] == ["vm-data"]
    assert (DataVolume, "vm-root") in cluster.objects
    assert disk_names(volumes(cluster.vm))["data"] == "vm-data"


@pytest.mark.parametrize("targets", [["missing"], ["root", "root"], []])
@pytest.mark.anyio
async def test_invalid_targets_do_not_modify_cluster(cluster, targets):
    before = deepcopy(cluster.objects)
    with pytest.raises(ValueError):
        await begin(storage(cluster), targets)
    assert cluster.objects == before


@pytest.mark.anyio
async def test_delayed_binding_fails_before_allocating_or_stopping(cluster):
    cluster.objects[StorageClass, "fast"]["volumeBindingMode"] = "WaitForFirstConsumer"
    before = deepcopy(cluster.objects)
    with pytest.raises(ValueError, match="WaitForFirstConsumer"):
        await begin(storage(cluster), ["root"])
    assert cluster.objects == before


@pytest.mark.anyio
async def test_storage_defaults_use_the_existing_pvcs_resolved_class_and_mode(cluster):
    driver = storage(cluster, disks={"root": {"size": "2Gi"}})
    await begin(driver, ["root"])
    spec = operation(cluster.vm)["disks"]["root"]["storage"]
    assert spec == {
        "resources": {"requests": {"storage": "2Gi"}},
        "accessModes": ["ReadWriteOnce"],
        "storageClassName": "fast",
        "volumeMode": "Block",
    }


@pytest.mark.parametrize(
    "url", ["", "http://upload.test", "https://user:password@upload.test", "https://upload.test?token=x"]
)
@pytest.mark.anyio
async def test_upload_configuration_validated_before_cluster_changes(cluster, url):
    before = deepcopy(cluster.objects)
    with pytest.raises(ValueError, match="HTTPS"):
        await begin(storage(cluster, upload_proxy_url=url), ["root"])
    assert cluster.objects == before


@pytest.mark.anyio
async def test_upload_streams_bytes_and_bearer_token_to_cdi_async_endpoint():
    received = []

    async def upload(request):
        received.append((request.path, request.headers["Authorization"], await request.read()))
        return web.Response()

    app = web.Application()
    app.router.add_post("/v1beta1/upload-async", upload)
    async with TestServer(app) as server:
        flasher = storage(Cluster())._flash()
        # Configuration validation requires HTTPS in production; this isolates the HTTP protocol.
        flasher.upload_proxy_url = str(server.make_url(""))
        await flasher._upload(Bytes(b"image bytes" * 1000), "upload-token")
    assert received == [("/v1beta1/upload-async", "Bearer upload-token", b"image bytes" * 1000)]


def test_compressed_local_image_uses_generic_resource_decoding(cluster, tmp_path):
    path = tmp_path / "image.raw.gz"
    payload = b"boot image" * 50000
    path.write_bytes(gzip.compress(payload))
    with serve(storage(cluster)) as client:
        result = CliRunner().invoke(client.cli(), ["flash", str(path), "--compression", "gzip"])
        assert result.exit_code == 0, result.exception
    assert cluster.uploads[disk_names(volumes(cluster.vm))["root"]] == payload


@pytest.mark.anyio
async def test_remote_url_flashes_through_exporter_resource_stream(cluster):
    payload = b"remote disk image" * 2000

    async def download(request):
        return web.Response(body=payload)

    app = web.Application()
    app.router.add_get("/disk.qcow2", download)
    async with TestServer(app) as server:

        def flash():
            with serve(storage(cluster)) as client:
                client.flash(str(server.make_url("/disk.qcow2")))

        await asyncio.to_thread(flash)
    assert cluster.uploads[disk_names(volumes(cluster.vm))["root"]] == payload


@pytest.mark.anyio
async def test_presigned_urls_are_created_after_slow_volume_preparation(cluster, monkeypatch):
    elapsed = 0
    create = cluster.create

    async def slow_create(obj):
        nonlocal elapsed
        if isinstance(obj, DataVolume):
            elapsed += 90  # Provisioning takes longer than OpenDAL's 60-second URL lifetime.
        return await create(obj)

    monkeypatch.setattr(cluster, "create", slow_create)

    async def download(request):
        if elapsed > int(request.query["expires"]):
            return web.Response(status=403)
        return web.Response(body=b"signed image")

    app = web.Application()
    app.router.add_get("/disk", download)
    async with TestServer(app) as server:

        async def presign(_path, expire_second):
            return SimpleNamespace(
                url=str(server.make_url(f"/disk?expires={elapsed + expire_second}")),
                headers={},
                method="GET",
            )

        operator = MagicMock()
        operator.capability.return_value.presign_read = True
        operator.to_async_operator.return_value = operator
        operator.presign_read = AsyncMock(side_effect=presign)

        def flash():
            with serve(storage(cluster)) as client:
                client.flash({"root": "os.raw", "data": "data.raw"}, operator=operator)

        await asyncio.to_thread(flash)
    assert len(cluster.uploads) == 2
    assert all(data == b"signed image" for data in cluster.uploads.values())


@pytest.mark.anyio
async def test_patch_with_a_stale_resource_version_is_refused(cluster):
    control = VMControl(cluster, "ns", "vm")
    stale = await control.get()
    # virt-controller rewrote the VM between this read and the write.
    cluster.objects[VirtualMachine, "vm"]["metadata"]["resourceVersion"] = "2"
    with pytest.raises(ApiError) as excinfo:
        await control.patch(stale, spec={"runStrategy": "Halted"})
    # Every phase, pending-operation and abort fence relies on this precondition.
    assert excinfo.value.status.code == 409
    assert cluster.vm["spec"]["runStrategy"] == "Always"


@pytest.mark.anyio
async def test_begin_retries_when_the_vm_changes_while_disk_specs_are_resolved(cluster):
    driver = storage(cluster)
    cluster.race_patch = cluster.patch_count + 1
    op = await begin(driver, ["root"])
    assert operation(cluster.vm)["id"] == op
    assert operation(cluster.vm)["phase"] == "staging"


@pytest.mark.anyio
async def test_a_second_begin_preempts_an_unfinished_flash(cluster):
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    abandoned = operation(cluster.vm)["disks"]["root"]["new"]
    new = await begin(driver, ["data"])
    state = operation(cluster.vm)
    assert state["id"] == new
    assert set(state["disks"]) == {"data"}
    assert abandoned in cluster.deleted
    assert (DataVolume, abandoned) not in cluster.objects
    with pytest.raises(RuntimeError, match="no longer active"):
        await stage(driver, op, "root")


@pytest.mark.anyio
async def test_a_cancelled_upload_is_reclaimed_by_the_next_flash(cluster, monkeypatch):
    entered, blocked = asyncio.Event(), True
    upload = Flash._upload

    async def blocking_upload(self, stream, token):
        if blocked:
            entered.set()
            await asyncio.Event().wait()
        return await upload(self, stream, token)

    monkeypatch.setattr(Flash, "_upload", blocking_upload)
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    abandoned = operation(cluster.vm)["disks"]["root"]["new"]
    task = asyncio.create_task(stage(driver, op, "root"))  # noqa: TID251
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    blocked = False
    # No recover call: the same driver's next flash reclaims the cancelled upload.
    new = await begin(driver, ["data"])
    await stage(driver, new, "data")
    await driver.commit_flash(new)
    state = operation(cluster.vm)
    assert state["phase"] == "complete"
    assert disk_names(volumes(cluster.vm))["data"] == state["disks"]["data"]["new"]
    assert abandoned in cluster.deleted
    assert (DataVolume, abandoned) not in cluster.objects


@pytest.mark.anyio
async def test_preemption_waits_for_the_preempted_flashes_claim(cluster, monkeypatch):
    driver = storage(cluster, shutdown_timeout=2)
    await begin(driver, ["root"])
    abandoned = operation(cluster.vm)["disks"]["root"]["new"]
    # The terminating upload pod holds the claim for a while after its DataVolume
    # is gone, and it still counts against the namespace quota.
    cluster.put(PersistentVolumeClaim, abandoned, {"spec": {"storageClassName": "fast"}})
    cluster.pvc_linger = 0.1
    create = cluster.create

    async def after_the_claim_is_released(obj):
        assert (PersistentVolumeClaim, abandoned) not in cluster.objects
        return await create(obj)

    monkeypatch.setattr(cluster, "create", after_the_claim_is_released)
    new = await begin(driver, ["data"])
    assert operation(cluster.vm)["id"] == new
    assert abandoned in cluster.deleted


@pytest.mark.anyio
async def test_a_racing_cleanup_of_the_same_volume_is_tolerated(cluster, monkeypatch):
    await begin(storage(cluster), ["root"])
    name = operation(cluster.vm)["disks"]["root"]["new"]
    delete = cluster.delete

    async def yielding_delete(kind, name, **kwargs):
        # A second cleanup reads the volume before this delete lands.
        await asyncio.sleep(0)
        return await delete(kind, name, **kwargs)

    monkeypatch.setattr(cluster, "delete", yielding_delete)
    flasher = storage(cluster)._flash()
    results = await asyncio.gather(  # noqa: TID251
        flasher._delete_owned(name, "vm-uid"),
        flasher._delete_owned(name, "vm-uid"),
        return_exceptions=True,
    )
    assert [result for result in results if isinstance(result, BaseException)] == []
    assert name in cluster.deleted


@pytest.mark.anyio
async def test_cleanup_releases_the_staged_volumes_together(cluster, monkeypatch):
    driver = storage(cluster, shutdown_timeout=5)
    await begin(driver, ["root", "data"])
    state = operation(cluster.vm)
    claims = set()
    for target in ("root", "data"):
        new = state["disks"][target]["new"]
        # Each claim is held by a terminating upload pod, and only leaves once
        # every delete has been issued.
        claim = f"{new}-claim"
        cluster.objects[DataVolume, new]["status"]["claimName"] = claim
        cluster.put(PersistentVolumeClaim, claim, {"spec": {"storageClassName": "fast"}})
        claims.add(claim)
    deleted = []
    delete = cluster.delete

    async def record_delete(kind, name, **kwargs):
        deleted.append(name)
        return await delete(kind, name, **kwargs)

    monkeypatch.setattr(cluster, "delete", record_delete)

    async def release_once_every_delete_is_issued():
        while len(deleted) < 2:
            await asyncio.sleep(0.001)
        for claim in claims:
            await delete(PersistentVolumeClaim, claim)

    asyncio.get_running_loop().create_task(release_once_every_delete_is_issued())
    # A cleanup that went one volume at a time would hold the first claim before
    # deleting the second, and time out; the volumes are released together.
    new = await begin(driver, ["logs"])
    assert operation(cluster.vm)["id"] == new
    assert len(deleted) == 2


def test_a_preempted_flash_has_no_leftovers_to_warn_about(cluster, caplog, monkeypatch, tmp_path):
    original = Flash.stage

    async def preempt_and_stage(self, operation_id, target, resource):
        # A second flash replaces this one between its RPCs.
        await begin(storage(cluster), ["data"])
        return await original(self, operation_id, target, resource)

    monkeypatch.setattr(Flash, "stage", preempt_and_stage)
    image = tmp_path / "os.raw"
    image.write_bytes(b"boot image")
    failure = pytest.RaisesExc(DriverError, match="no longer active")
    with serve(storage(cluster)) as client, pytest.RaisesGroup(failure):
        client.flash(image)
    assert "Flash interrupted" not in caplog.text
    assert operation(cluster.vm)["phase"] == "staging"


@pytest.mark.anyio
async def test_begin_reclaims_a_flash_abandoned_by_an_earlier_lease(cluster):
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    abandoned = operation(cluster.vm)["disks"]["root"]["new"]
    new = await begin(storage(cluster), ["data"])
    state = operation(cluster.vm)
    assert state["id"] == new
    assert set(state["disks"]) == {"data"}
    assert abandoned in cluster.deleted
    assert (DataVolume, abandoned) not in cluster.objects


@pytest.mark.anyio
async def test_begin_finishes_an_abort_an_earlier_flash_started(cluster):
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    abandoned = operation(cluster.vm)["disks"]["root"]["new"]
    state = operation(cluster.vm)
    state["phase"] = "aborting"
    cluster.objects[VirtualMachine, "vm"]["metadata"]["annotations"][FLASH_ANNOTATION] = json.dumps(state)
    new = await begin(driver, ["data"])
    assert operation(cluster.vm)["id"] == new
    assert abandoned in cluster.deleted


@pytest.mark.anyio
async def test_shutdown_cancels_a_reclaim_still_running(cluster, monkeypatch):
    await begin(storage(cluster), ["root"])
    entered = asyncio.Event()

    async def stuck_recover(self, operation_id):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Flash, "recover", stuck_recover)
    fresh = storage(cluster)
    fresh.reset()
    await entered.wait()
    fresh.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await fresh._reclaim


@pytest.mark.anyio
async def test_session_start_reclaims_a_flash_left_by_an_earlier_lease(cluster):
    driver = storage(cluster)
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    abandoned = operation(cluster.vm)["disks"]["root"]["new"]
    fresh = storage(cluster)
    fresh.reset()
    await asyncio.wait_for(fresh._reclaim, 2)  # noqa: TID251
    assert operation(cluster.vm)["phase"] == "aborted"
    assert abandoned in cluster.deleted
    assert (DataVolume, abandoned) not in cluster.objects


@pytest.mark.anyio
async def test_cleanup_does_not_forget_a_disk_still_used_by_a_vmi(cluster):
    driver = storage(cluster)
    old_volumes = deepcopy(volumes(cluster.vm))
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    cluster.fail_delete = True
    await driver.commit_flash(op)
    cluster.fail_delete = False
    await power(cluster).on()
    cluster.vmi = {"metadata": {"name": "vm"}, "spec": {"volumes": old_volumes}}
    with pytest.raises(RuntimeError, match="still uses"):
        await begin(storage(cluster), ["data"])
    assert (DataVolume, "vm-root") in cluster.objects
    assert operation(cluster.vm)["phase"] == "committed"
    cluster.vmi = None
    await begin(storage(cluster), ["data"])
    assert cluster.deleted == ["vm-root"]


@pytest.mark.anyio
async def test_recover_aborts_after_the_vm_disk_references_were_edited(cluster):
    await begin(storage(cluster), ["root"])
    new = operation(cluster.vm)["disks"]["root"]["new"]
    for volume in volumes(cluster.objects[VirtualMachine, "vm"]):
        if volume["name"] == "root":
            volume["dataVolume"]["name"] = new
    # Recovery deletes only staged volumes the VM does not reference, so an edited VM
    # must not wedge it: both recovery and the next flash have to keep working.
    fresh = storage(cluster)
    fresh.reset()
    assert await asyncio.wait_for(fresh._reclaim, 2) is False  # noqa: TID251
    assert operation(cluster.vm)["phase"] == "aborted"
    assert (DataVolume, new) in cluster.objects
    assert disk_names(volumes(cluster.vm))["root"] == new
    following = await begin(storage(cluster), ["data"])
    assert operation(cluster.vm)["id"] == following


@pytest.mark.anyio
async def test_reclaim_does_not_touch_an_operation_that_replaced_the_one_it_read(cluster):
    await begin(storage(cluster), ["root"])
    stale = cluster.vm
    # Another client started a new operation between that read and the recovery.
    state = operation(cluster.vm)
    state["id"] = uuid4().hex
    cluster.objects[VirtualMachine, "vm"]["metadata"]["annotations"][FLASH_ANNOTATION] = json.dumps(state)
    flasher = storage(cluster)._flash()
    with pytest.raises(RuntimeError, match="different flash operation"):
        await flasher.reclaim(stale)
    assert operation(cluster.vm)["phase"] == "staging"


@pytest.mark.anyio
async def test_processing_timeout_can_be_recovered(cluster):
    cluster.complete_uploads = False
    driver = storage(cluster, flash_timeout=0.02)
    op = await begin(driver, ["root"])
    with pytest.raises(TimeoutError):
        await stage(driver, op, "root")
    assert await driver.recover_flash(op) is False
    assert not cluster.activations
    assert disk_names(volumes(cluster.vm))["root"] == "vm-root"


@pytest.mark.parametrize("status", [302, 401, 500])
@pytest.mark.anyio
async def test_upload_rejects_errors_and_does_not_follow_redirects(status):
    followed = []

    async def upload(request):
        await request.read()
        return web.Response(status=status, headers={"Location": "/redirected"})

    async def redirected(request):
        followed.append(request.headers.get("Authorization"))
        return web.Response()

    app = web.Application()
    app.router.add_post("/v1beta1/upload-async", upload)
    app.router.add_route("*", "/redirected", redirected)
    async with TestServer(app) as server:
        flasher = storage(Cluster())._flash()
        flasher.upload_proxy_url = str(server.make_url(""))
        with pytest.raises(RuntimeError, match=f"HTTP {status}"):
            await flasher._upload(Bytes(b"image"), "upload-token")
    assert not followed


@pytest.mark.anyio
async def test_interrupted_begin_does_not_lock_out_the_lease(cluster, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = Flash._wait_volume

    async def slow(self, name, phase):
        if phase == "UploadReady":
            entered.set()
            await release.wait()
        return await original(self, name, phase)

    monkeypatch.setattr(Flash, "_wait_volume", slow)
    driver = storage(cluster)
    task = asyncio.create_task(begin(driver, ["root"]))  # noqa: TID251
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    # The cancelled call discarded its own operation, so this lease can flash again.
    await begin(driver, ["root"])
    assert operation(cluster.vm)["phase"] == "staging"


def test_provisioning_failure_reaches_the_client_readably(cluster, tmp_path, monkeypatch):
    image = tmp_path / "os.raw"
    image.write_bytes(b"boot image")
    create = cluster.create

    async def failing(obj):
        result = await create(obj)
        if isinstance(obj, DataVolume):
            cluster.objects[DataVolume, obj.metadata.name]["status"] = {"phase": "Failed"}
        return result

    monkeypatch.setattr(cluster, "create", failing)
    with serve(storage(cluster)) as client, pytest.raises(Exception, match="Failed"):
        client.flash(image)


@pytest.mark.anyio
async def test_begin_fails_fast_when_an_upload_pod_never_starts(cluster, monkeypatch):
    driver = storage(cluster, provision_timeout=0.05, poll_interval=0.01)
    create = cluster.create

    async def pending(obj):
        result = await create(obj)
        if isinstance(obj, DataVolume):
            cluster.objects[DataVolume, obj.metadata.name]["status"] = {
                "phase": "Pending",
                "conditions": [{"type": "Bound", "reason": "Pending", "message": "exceeded quota: golden"}],
            }
        return result

    monkeypatch.setattr(cluster, "create", pending)
    # The quota message reaches the caller instead of a TaskGroup wrapper, and begin's
    # own provisioning timeout bounds the wait rather than the shorter stage timeout.
    started = asyncio.get_running_loop().time()
    with pytest.raises(RuntimeError, match="exceeded quota"):
        await begin(driver, ["root"])
    assert asyncio.get_running_loop().time() - started < 5
    monkeypatch.undo()
    assert operation(cluster.vm)["phase"] == "aborted"
    await begin(driver, ["root"])


@pytest.mark.anyio
async def test_begin_fails_fast_when_cdi_reports_a_volume_that_will_never_bind(cluster, monkeypatch):
    driver = storage(cluster, provision_timeout=2, poll_interval=0.01, fatal_grace=0.05)
    create = cluster.create

    async def unbindable(obj):
        result = await create(obj)
        if isinstance(obj, DataVolume):
            cluster.objects[DataVolume, obj.metadata.name]["status"] = {
                "phase": "Pending",
                "conditions": [
                    {
                        "type": "Bound",
                        "status": "False",
                        "reason": "ErrExceededQuota",
                        "message": "exceeded quota: golden",
                    }
                ],
            }
        return result

    monkeypatch.setattr(cluster, "create", unbindable)
    started = asyncio.get_running_loop().time()
    with pytest.raises(RuntimeError, match="exceeded quota"):
        await begin(driver, ["root"])
    # No reason to wait out a five-minute provisioning timeout for this volume.
    assert asyncio.get_running_loop().time() - started < 1


@pytest.mark.anyio
async def test_a_quota_condition_that_clears_is_waited_out(cluster, monkeypatch):
    driver = storage(cluster, poll_interval=0.01, fatal_grace=0.5)
    create = cluster.create

    async def briefly_unbindable(obj):
        result = await create(obj)
        if isinstance(obj, DataVolume):
            cluster.objects[DataVolume, obj.metadata.name]["status"] = {
                "phase": "Pending",
                "conditions": [
                    {
                        "type": "Bound",
                        "status": "False",
                        "reason": "ErrExceededQuota",
                        "message": "exceeded quota: golden",
                    }
                ],
            }

            async def bind():
                # A quota frees up, or a preempted flash's claim is finally
                # released; CDI retries the claim and the volume proceeds.
                await asyncio.sleep(0.05)
                cluster.objects[DataVolume, obj.metadata.name]["status"] = {"phase": "UploadReady"}

            asyncio.get_running_loop().create_task(bind())
        return result

    monkeypatch.setattr(cluster, "create", briefly_unbindable)
    op = await begin(driver, ["root"])
    assert operation(cluster.vm)["id"] == op
    assert operation(cluster.vm)["phase"] == "staging"


@pytest.mark.anyio
async def test_stage_waits_for_an_upload_pod_that_restarted(cluster):
    driver = storage(cluster, poll_interval=0.01)
    op = await begin(driver, ["root"])
    name = operation(cluster.vm)["disks"]["root"]["new"]
    # The upload pod restarted before staging; the upload is still served once it is back.
    cluster.objects[DataVolume, name]["status"] = {"phase": "Pending", "restartCount": 1}

    async def restart():
        await asyncio.sleep(0.05)
        cluster.objects[DataVolume, name]["status"] = {"phase": "UploadReady", "restartCount": 1}

    restarting = asyncio.create_task(restart())  # noqa: TID251
    await stage(driver, op, "root")
    await restarting
    assert cluster.uploads[name] == b"image"


@pytest.mark.anyio
async def test_recover_ignores_an_operation_that_was_never_written(cluster):
    driver = storage(cluster)
    assert await driver.recover_flash("never-written") is False
    op = await begin(driver, ["root"])
    await stage(driver, op, "root")
    await driver.commit_flash(op)
    # The VM carries a finished operation of its own; another id must not be an error.
    assert await driver.recover_flash("never-written") is False
    assert await driver.recover_flash(op) is True
    driver = storage(cluster)
    await begin(driver, ["root"])
    with pytest.raises(RuntimeError, match="different flash operation"):
        await driver.recover_flash("never-written")
