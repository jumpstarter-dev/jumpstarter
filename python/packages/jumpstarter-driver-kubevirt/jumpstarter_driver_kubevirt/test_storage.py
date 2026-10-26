"""Behavioral tests with real Jumpstarter streams and a stateful Kubernetes API."""

import asyncio
import gzip
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from click.testing import CliRunner
from lightkube.core.exceptions import ApiError
from lightkube.models.meta_v1 import Status
from lightkube.resources.core_v1 import PersistentVolumeClaim
from lightkube.resources.storage_v1 import StorageClass
from opendal.exceptions import NotFound

from .driver import KubevirtPower, KubevirtStorage
from .storage import Disk, Flash, disk_names, volumes
from .vm import DataVolume, UploadTokenRequest, VirtualMachine, VirtualMachineInstance, operation
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve


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
        self.vmi = None
        self.before_patch = None
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

    async def patch(self, kind, name, obj, **kwargs):
        if self.before_patch:
            callback, self.before_patch = self.before_patch, None
            await callback()
        current = self.objects[kind, name]
        if obj["metadata"]["resourceVersion"] != current["metadata"]["resourceVersion"]:
            raise api_error(409)
        if obj["metadata"]["uid"] != current["metadata"]["uid"]:
            raise api_error(409)

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

    async def upload(self, stream, token):
        chunks = [chunk async for chunk in stream]
        data = b"".join(chunks)
        self.uploads[token] = data
        phase = "Succeeded" if self.complete_uploads else "UploadInProgress"
        if data == self.fail_upload:
            phase = "Failed"
        self.objects[DataVolume, token]["status"] = {"phase": phase}


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


async def stage(driver, operation_id, target, data=b"image"):
    await driver.prepare_flash(operation_id, target)
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
    with serve(storage(cluster)) as client, pytest.RaisesGroup(pytest.RaisesExc(DriverError, match="Failed")):
        client.flash({"root": root, "data": data})
    assert cluster.vm["spec"] == before
    assert not cluster.activations
    assert operation(cluster.vm)["phase"] == "aborted"
    assert {name for kind, name in cluster.objects if kind is DataVolume} == {"vm-root", "vm-data", "vm-logs"}


@pytest.mark.parametrize(
    "args", [[], ["one", "two"], ["one", "-t", "data:two"], ["-t", "root:a", "-t", "root:b"], ["-t", "data:"]]
)
def test_cli_rejects_ambiguous_inputs_before_changing_cluster(cluster, args):
    before = deepcopy(cluster.objects)
    with serve(storage(cluster)) as client:
        result = CliRunner().invoke(client.cli(), ["flash", *args])
    assert result.exit_code == 2
    assert cluster.objects == before


def test_missing_later_file_aborts_prepared_disks(cluster, tmp_path):
    root = tmp_path / "os.raw"
    root.write_bytes(b"boot image")
    with serve(storage(cluster)) as client, pytest.raises(NotFound):
        client.flash({"root": root, "data": tmp_path / "missing"})
    assert disk_names(volumes(cluster.vm))["root"] == "vm-root"
    assert not cluster.activations
    assert operation(cluster.vm)["phase"] == "aborted"


@pytest.mark.asyncio
async def test_commit_requires_every_disk_to_finish(cluster):
    driver = storage(cluster)
    op = await driver.begin_flash(["root", "data"])
    await stage(driver, op, "root")
    with pytest.raises(ApiError):
        await driver.commit_flash(op)
    assert cluster.vm["spec"]["runStrategy"] == "Always"
    assert not cluster.activations


@pytest.mark.asyncio
async def test_power_is_blocked_until_post_upload_processing_finishes(cluster):
    driver = storage(cluster)
    cluster.complete_uploads = False
    op = await driver.begin_flash(["root"])
    task = asyncio.create_task(stage(driver, op, "root"))
    while not cluster.uploads:
        await asyncio.sleep(0)
    assert not task.done()
    with pytest.raises(RuntimeError, match="unfinished"):
        await power(cluster).on()
    name = next(iter(cluster.uploads))
    cluster.objects[DataVolume, name]["status"]["phase"] = "Succeeded"
    await task
    await driver.commit_flash(op)
    assert disk_names(volumes(cluster.vm))["root"] == name


@pytest.mark.asyncio
async def test_cancelled_upload_recovers_after_exporter_restart(cluster, monkeypatch):
    entered = asyncio.Event()

    async def blocked_upload(_self, stream, token):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Flash, "_upload", blocked_upload)
    driver = storage(cluster)
    op = await driver.begin_flash(["root"])
    task = asyncio.create_task(stage(driver, op, "root"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await storage(cluster).recover_flash() is False
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


@pytest.mark.asyncio
async def test_cleanup_failure_is_retryable_without_rolling_back_or_blocking_power(cluster):
    driver = storage(cluster)
    op = await driver.begin_flash(["root"])
    await stage(driver, op, "root")
    cluster.fail_delete = True
    await driver.commit_flash(op)
    assert operation(cluster.vm)["phase"] == "committed"
    active = disk_names(volumes(cluster.vm))["root"]
    await power(cluster).on()
    cluster.fail_delete = False
    assert await storage(cluster).recover_flash() is True
    assert disk_names(volumes(cluster.vm))["root"] == active
    assert (DataVolume, active) in cluster.objects
    assert operation(cluster.vm)["phase"] == "complete"


@pytest.mark.asyncio
async def test_recovery_fences_a_commit_waiting_for_shutdown(cluster):
    driver = storage(cluster, shutdown_timeout=2)
    cluster.vmi = {"metadata": {"name": "vm"}, "spec": {"volumes": deepcopy(volumes(cluster.vm))}}
    cluster.stop_after = 10000
    op = await driver.begin_flash(["root"])
    await stage(driver, op, "root")
    task = asyncio.create_task(driver.commit_flash(op))
    while cluster.vm["spec"]["runStrategy"] != "Halted":
        await asyncio.sleep(0)
    await storage(cluster).recover_flash()
    cluster.stop_after = 0
    with pytest.raises(RuntimeError, match="no longer active"):
        await task
    assert not cluster.activations
    assert disk_names(volumes(cluster.vm))["root"] == "vm-root"


@pytest.mark.asyncio
async def test_shutdown_timeout_preserves_disk_references(cluster):
    driver = storage(cluster, shutdown_timeout=0.01)
    cluster.vmi = {"metadata": {"name": "vm"}, "spec": {"volumes": deepcopy(volumes(cluster.vm))}}
    cluster.stop_after = 10000
    op = await driver.begin_flash(["root"])
    await stage(driver, op, "root")
    with pytest.raises(TimeoutError):
        await driver.commit_flash(op)
    await driver.recover_flash()
    assert not cluster.activations
    assert disk_names(volumes(cluster.vm))["root"] == "vm-root"


@pytest.mark.asyncio
async def test_power_update_cannot_race_past_begin_flash(cluster):
    driver = storage(cluster)
    cluster.before_patch = lambda: driver.begin_flash(["root"])
    with pytest.raises(ApiError) as error:
        await power(cluster).on()
    assert error.value.status.code == 409
    assert operation(cluster.vm)["phase"] == "staging"


@pytest.mark.asyncio
async def test_named_disks_preserve_unselected_templates_and_external_volumes(cluster):
    vm = cluster.objects[VirtualMachine, "vm"]
    vm["spec"]["dataVolumeTemplates"] = [
        {"metadata": {"name": name}, "spec": {"source": {"blank": {}}}} for name in ("vm-root", "vm-data")
    ]
    cluster.objects[DataVolume, "vm-root"]["metadata"]["ownerReferences"] = []
    driver = storage(cluster)
    op = await driver.begin_flash(["root"])
    await stage(driver, op, "root")
    await driver.commit_flash(op)
    assert [t["metadata"]["name"] for t in cluster.vm["spec"]["dataVolumeTemplates"]] == ["vm-data"]
    assert (DataVolume, "vm-root") in cluster.objects
    assert disk_names(volumes(cluster.vm))["data"] == "vm-data"


@pytest.mark.parametrize("targets", [["missing"], ["root", "root"], []])
@pytest.mark.asyncio
async def test_invalid_targets_do_not_modify_cluster(cluster, targets):
    before = deepcopy(cluster.objects)
    with pytest.raises(ValueError):
        await storage(cluster).begin_flash(targets)
    assert cluster.objects == before


@pytest.mark.asyncio
async def test_delayed_binding_fails_before_allocating_or_stopping(cluster):
    cluster.objects[StorageClass, "fast"]["volumeBindingMode"] = "WaitForFirstConsumer"
    before = deepcopy(cluster.objects)
    with pytest.raises(ValueError, match="WaitForFirstConsumer"):
        await storage(cluster).begin_flash(["root"])
    assert cluster.objects == before


@pytest.mark.asyncio
async def test_storage_defaults_use_the_existing_pvcs_resolved_class_and_mode(cluster):
    driver = storage(cluster, disks={"root": {"size": "2Gi"}})
    await driver.begin_flash(["root"])
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
@pytest.mark.asyncio
async def test_upload_configuration_validated_before_cluster_changes(cluster, url):
    before = deepcopy(cluster.objects)
    with pytest.raises(ValueError, match="HTTPS"):
        await storage(cluster, upload_proxy_url=url).begin_flash(["root"])
    assert cluster.objects == before


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_begin_rechecks_operation_after_finishing_old_cleanup(cluster, monkeypatch):
    driver = storage(cluster)
    op = await driver.begin_flash(["root"])
    await stage(driver, op, "root")
    cluster.fail_delete = True
    await driver.commit_flash(op)
    cluster.fail_delete = False
    recover = Flash.recover
    new_id = None

    async def recover_then_start_another(self, operation_id):
        nonlocal new_id
        result = await recover(self, operation_id)
        new_id = await storage(cluster).begin_flash(["data"])
        return result

    monkeypatch.setattr(Flash, "recover", recover_then_start_another)
    with pytest.raises(RuntimeError, match="unfinished"):
        await driver.begin_flash(["root"])
    assert operation(cluster.vm)["id"] == new_id
    assert set(operation(cluster.vm)["disks"]) == {"data"}


@pytest.mark.asyncio
async def test_cleanup_does_not_forget_a_disk_still_used_by_a_vmi(cluster):
    driver = storage(cluster)
    old_volumes = deepcopy(volumes(cluster.vm))
    op = await driver.begin_flash(["root"])
    await stage(driver, op, "root")
    cluster.fail_delete = True
    await driver.commit_flash(op)
    cluster.fail_delete = False
    await power(cluster).on()
    cluster.vmi = {"metadata": {"name": "vm"}, "spec": {"volumes": old_volumes}}
    with pytest.raises(RuntimeError, match="still uses"):
        await driver.recover_flash()
    assert (DataVolume, "vm-root") in cluster.objects
    assert operation(cluster.vm)["phase"] == "committed"
    cluster.vmi = None
    assert await driver.recover_flash() is True
    assert cluster.deleted == ["vm-root"]


@pytest.mark.asyncio
async def test_processing_timeout_can_be_recovered(cluster):
    cluster.complete_uploads = False
    driver = storage(cluster, flash_timeout=0.02)
    op = await driver.begin_flash(["root"])
    with pytest.raises(TimeoutError):
        await stage(driver, op, "root")
    assert await driver.recover_flash(op) is False
    assert not cluster.activations
    assert disk_names(volumes(cluster.vm))["root"] == "vm-root"


@pytest.mark.parametrize("status", [302, 401, 500])
@pytest.mark.asyncio
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
