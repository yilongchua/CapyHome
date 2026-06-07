import csv

import pytest

from src.config.paths import Paths
from src.gateway.routers import workflow as workflow_router


def _patch_paths(monkeypatch, tmp_path):
    paths = Paths(tmp_path)
    monkeypatch.setattr(workflow_router, "get_paths", lambda: paths)
    return paths


def _write_source(paths: Paths, thread_id: str) -> None:
    uploads = paths.sandbox_uploads_dir(thread_id)
    uploads.mkdir(parents=True, exist_ok=True)
    with (uploads / "address_coy.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "company_id", "name", "country", "address"])
        writer.writeheader()
        writer.writerow({"id": "1", "company_id": "c1", "name": "Example A", "country": "SG", "address": "Raffles"})
        writer.writerow({"id": "2", "company_id": "c2", "name": "Example B", "country": "SG", "address": "Orchard"})


def _workflow_payload() -> dict:
    return {
        "version": "1",
        "source": {
            "path": "/mnt/user-data/workspace/uploads/address_coy.csv",
            "type": "csv",
            "columns": [],
            "row_count": 0,
        },
        "runtime": {
            "workflow_json": "/mnt/user-data/workspace/runtime/workflow.json",
            "sqlite": "/mnt/user-data/workspace/runtime/workflow.sqlite",
            "output_csv": "/mnt/user-data/workspace/uploads/address_coy_output.csv",
        },
        "row_task": {
            "instruction": "Find full address",
            "input_fields": ["name", "country", "address"],
            "output_schema": {"full_address": "string"},
            "failure_value": "failed run",
            "no_result_value": "",
        },
        "execution": {
            "status": "ready",
            "max_parallel": 1,
            "flush_every_completed_rows": 20,
            "flush_all": False,
            "add_to_memory": False,
            "current_row_index": 0,
            "completed_rows": 0,
            "consecutive_failures": 0,
            "consecutive_failures_limit": 5,
            "failure_rows": [],
        },
    }


def test_output_virtual_path_for_source():
    assert (
        workflow_router.output_virtual_path_for_source("/mnt/user-data/workspace/uploads/address_coy.csv")
        == "/mnt/user-data/workspace/uploads/address_coy_output.csv"
    )
    assert (
        workflow_router.output_virtual_path_for_source("/mnt/user-data/workspace/uploads/companies.v2.csv")
        == "/mnt/user-data/workspace/uploads/companies.v2_output.csv"
    )


def test_initialize_imports_csv_to_sqlite(monkeypatch, tmp_path):
    thread_id = "thread-1"
    paths = _patch_paths(monkeypatch, tmp_path)
    _write_source(paths, thread_id)
    workflow_router.write_workflow(thread_id, _workflow_payload())

    initialized = workflow_router.initialize_runtime(thread_id, workflow_router.read_workflow(thread_id))

    assert initialized["source"]["columns"] == ["id", "company_id", "name", "country", "address"]
    assert initialized["source"]["row_count"] == 2
    claimed = workflow_router.claim_rows(thread_id, initialized)
    assert [row["row_number"] for row in claimed] == ["1"]
    assert claimed[0]["source"]["name"] == "Example A"


def test_normalize_workflow_defaults_child_memory_off(monkeypatch, tmp_path):
    thread_id = "thread-1"
    _patch_paths(monkeypatch, tmp_path)
    payload = _workflow_payload()
    payload["execution"].pop("add_to_memory")

    normalized = workflow_router.normalize_workflow(thread_id, payload)

    assert normalized["execution"]["add_to_memory"] is False


def test_result_accounting_and_export(monkeypatch, tmp_path):
    thread_id = "thread-1"
    paths = _patch_paths(monkeypatch, tmp_path)
    _write_source(paths, thread_id)
    workflow_router.write_workflow(thread_id, _workflow_payload())
    data = workflow_router.initialize_runtime(thread_id, workflow_router.read_workflow(thread_id))

    data = workflow_router.record_row_result(
        thread_id,
        data,
        0,
        status="success",
        result={"full_address": "1 Full Street, Singapore"},
        child_thread_id="child-1",
        child_run_id="run-1",
        error=None,
    )
    data = workflow_router.record_row_result(
        thread_id,
        data,
        1,
        status="failed",
        result={"full_address": "failed run"},
        child_thread_id="child-2",
        child_run_id="run-2",
        error="failed_run",
    )

    assert data["execution"]["completed_rows"] == 1
    assert data["execution"]["consecutive_failures"] == 1
    assert data["execution"]["failure_rows"] == ["2"]

    output_virtual = workflow_router.export_output_csv(thread_id, data)
    output_path = paths.resolve_virtual_path(thread_id, output_virtual)
    rows = list(csv.DictReader(output_path.open("r", encoding="utf-8", newline="")))
    assert rows[0]["full_address"] == "1 Full Street, Singapore"
    assert rows[1]["full_address"] == "failed run"


def test_custom_consecutive_failure_limit_stops_workflow(monkeypatch, tmp_path):
    thread_id = "thread-1"
    paths = _patch_paths(monkeypatch, tmp_path)
    _write_source(paths, thread_id)
    payload = _workflow_payload()
    payload["execution"]["consecutive_failures_limit"] = 1
    workflow_router.write_workflow(thread_id, payload)
    data = workflow_router.initialize_runtime(thread_id, workflow_router.read_workflow(thread_id))

    data = workflow_router.record_row_result(
        thread_id,
        data,
        0,
        status="failed",
        result={"full_address": "failed run"},
        child_thread_id="child-1",
        child_run_id="run-1",
        error="failed_run",
    )

    assert data["execution"]["consecutive_failures_limit"] == 1
    assert data["execution"]["consecutive_failures"] == 1
    assert data["execution"]["status"] == "stopped_failed_threshold"


def test_flush_every_completed_rows_crossing_uses_workflow_value():
    assert workflow_router.should_flush_completed_rows(19, 20, 20)
    assert workflow_router.should_flush_completed_rows(19, 21, 20)
    assert not workflow_router.should_flush_completed_rows(20, 21, 20)
    assert not workflow_router.should_flush_completed_rows(0, 0, 20)
    assert workflow_router.should_flush_completed_rows(2, 3, 3)


def test_processed_row_count_includes_failed_rows(monkeypatch, tmp_path):
    thread_id = "thread-1"
    paths = _patch_paths(monkeypatch, tmp_path)
    _write_source(paths, thread_id)
    workflow_router.write_workflow(thread_id, _workflow_payload())
    data = workflow_router.initialize_runtime(thread_id, workflow_router.read_workflow(thread_id))
    data = workflow_router.record_row_result(
        thread_id,
        data,
        0,
        status="success",
        result={"full_address": "1 Full Street, Singapore"},
        child_thread_id="child-success",
        child_run_id="run-success",
        error=None,
    )
    workflow_router.record_row_result(
        thread_id,
        data,
        1,
        status="failed",
        result={"full_address": "failed run"},
        child_thread_id="child-failed",
        child_run_id="run-failed",
        error="failed_run",
    )

    with workflow_router._connect(workflow_router.workflow_sqlite_path(thread_id)) as conn:
        assert workflow_router._processed_row_count(conn) == 2


@pytest.mark.anyio
async def test_flush_cleanup_keeps_failed_children_by_default(monkeypatch, tmp_path):
    thread_id = "thread-1"
    paths = _patch_paths(monkeypatch, tmp_path)
    _write_source(paths, thread_id)
    workflow_router.write_workflow(thread_id, _workflow_payload())
    data = workflow_router.initialize_runtime(thread_id, workflow_router.read_workflow(thread_id))
    data = workflow_router.record_row_result(
        thread_id,
        data,
        0,
        status="success",
        result={"full_address": "1 Full Street, Singapore"},
        child_thread_id="child-success",
        child_run_id="run-success",
        error=None,
    )
    workflow_router.record_row_result(
        thread_id,
        data,
        1,
        status="failed",
        result={"full_address": "failed run"},
        child_thread_id="child-failed",
        child_run_id="run-failed",
        error="failed_run",
    )

    class _Threads:
        def __init__(self):
            self.deleted: list[str] = []

        async def delete(self, thread_id):
            self.deleted.append(thread_id)

    class _Client:
        def __init__(self):
            self.threads = _Threads()

    client = _Client()
    await workflow_router._delete_flushed_children(client, thread_id, limit=20, flush_all=False)

    assert client.threads.deleted == ["child-success"]
    with workflow_router._connect(workflow_router.workflow_sqlite_path(thread_id)) as conn:
        rows = conn.execute("SELECT status, child_thread_id, child_run_id, error FROM workflow_rows ORDER BY row_index").fetchall()
    assert rows[0]["status"] == "success"
    assert rows[0]["child_thread_id"] is None
    assert rows[0]["child_run_id"] is None
    assert rows[0]["error"] == "child_thread_deleted"
    assert rows[1]["status"] == "failed"
    assert rows[1]["child_thread_id"] == "child-failed"
    assert rows[1]["child_run_id"] == "run-failed"
    assert rows[1]["error"] == "failed_run"


@pytest.mark.anyio
async def test_flush_all_deletes_failed_children(monkeypatch, tmp_path):
    thread_id = "thread-1"
    paths = _patch_paths(monkeypatch, tmp_path)
    _write_source(paths, thread_id)
    payload = _workflow_payload()
    payload["execution"]["flush_all"] = True
    workflow_router.write_workflow(thread_id, payload)
    data = workflow_router.initialize_runtime(thread_id, workflow_router.read_workflow(thread_id))
    data = workflow_router.record_row_result(
        thread_id,
        data,
        0,
        status="success",
        result={"full_address": "1 Full Street, Singapore"},
        child_thread_id="child-success",
        child_run_id="run-success",
        error=None,
    )
    workflow_router.record_row_result(
        thread_id,
        data,
        1,
        status="failed",
        result={"full_address": "failed run"},
        child_thread_id="child-failed",
        child_run_id="run-failed",
        error="failed_run",
    )

    class _Threads:
        def __init__(self):
            self.deleted: list[str] = []

        async def delete(self, thread_id):
            self.deleted.append(thread_id)

    class _Client:
        def __init__(self):
            self.threads = _Threads()

    client = _Client()
    await workflow_router._delete_flushed_children(client, thread_id, limit=20, flush_all=True)

    assert client.threads.deleted == ["child-success", "child-failed"]
    with workflow_router._connect(workflow_router.workflow_sqlite_path(thread_id)) as conn:
        rows = conn.execute("SELECT status, child_thread_id, child_run_id, error FROM workflow_rows ORDER BY row_index").fetchall()
    assert rows[0]["child_thread_id"] is None
    assert rows[0]["child_run_id"] is None
    assert rows[0]["error"] == "child_thread_deleted"
    assert rows[1]["status"] == "failed"
    assert rows[1]["child_thread_id"] is None
    assert rows[1]["child_run_id"] is None
    assert rows[1]["error"] == "failed_run"


def test_child_result_parsing():
    data = _workflow_payload()
    assert workflow_router.parse_child_result('{"full_address": ""}', data) == (
        "success",
        {"full_address": ""},
        None,
    )
    assert workflow_router.parse_child_result('{"full_address": "failed run"}', data) == (
        "failed",
        {"full_address": "failed run"},
        "failed_run",
    )
    status, result, error = workflow_router.parse_child_result("not-json", data)
    assert status == "failed"
    assert result is None
    assert error and error.startswith("invalid_json")


def test_recover_workflow_requeues_failed_rows(monkeypatch, tmp_path):
    thread_id = "thread-1"
    paths = _patch_paths(monkeypatch, tmp_path)
    _write_source(paths, thread_id)
    workflow_router.write_workflow(thread_id, _workflow_payload())
    data = workflow_router.initialize_runtime(thread_id, workflow_router.read_workflow(thread_id))

    data = workflow_router.record_row_result(
        thread_id,
        data,
        0,
        status="failed",
        result=None,
        child_thread_id="child-1",
        child_run_id="run-1",
        error="invalid_json",
    )
    recovered = workflow_router.recover_workflow(thread_id)

    assert recovered["execution"]["status"] == "ready"
    assert recovered["execution"]["consecutive_failures"] == 0
    assert recovered["execution"]["failure_rows"] == []
    assert recovered["execution"]["current_row_index"] == 0

    with workflow_router._connect(workflow_router.workflow_sqlite_path(thread_id)) as conn:
        row = conn.execute(
            "SELECT status, result_json, child_thread_id, child_run_id, error FROM workflow_rows WHERE row_index = 0"
        ).fetchone()
    assert row["status"] == "pending"
    assert row["result_json"] is None
    assert row["child_thread_id"] is None
    assert row["child_run_id"] is None
    assert row["error"] is None


@pytest.mark.anyio
async def test_child_row_run_uses_shared_config_recursion_limit(monkeypatch):
    class _AppConfig:
        def get_default_run_config(self):
            return {"recursion_limit": 1000}

    class _Threads:
        async def create(self):
            return {"thread_id": "child-thread"}

        async def get_state(self, thread_id):
            assert thread_id == "child-thread"
            return {"values": {"messages": [{"type": "ai", "content": '{"full_address": "1 Full Street"}'}]}}

    class _Runs:
        def __init__(self):
            self.create_kwargs = None

        async def create(self, *args, **kwargs):
            self.create_kwargs = kwargs
            return {"run_id": "child-run"}

        async def join(self, thread_id, run_id):
            assert (thread_id, run_id) == ("child-thread", "child-run")

    class _Client:
        def __init__(self):
            self.threads = _Threads()
            self.runs = _Runs()

    client = _Client()
    active = workflow_router._ActiveWorkflowRun()
    monkeypatch.setattr(workflow_router, "get_app_config", lambda: _AppConfig())
    row = {
        "row_index": 0,
        "row_number": "1",
        "source": {"name": "Example A", "country": "SG", "address": "Raffles"},
    }

    result = await workflow_router._execute_child_row(client, "parent-thread", _workflow_payload(), row, active)

    assert result[:3] == (0, "success", {"full_address": "1 Full Street"})
    assert client.runs.create_kwargs["config"] == {"recursion_limit": 1000}
    assert client.runs.create_kwargs["context"]["add_to_memory"] is False
    assert client.runs.create_kwargs["context"]["skip_title_generation"] is True
    assert client.runs.create_kwargs["context"]["compact_title"] == "wf r1"
    assert client.runs.create_kwargs["metadata"]["title"] == "wf r1"


@pytest.mark.anyio
async def test_child_row_run_honors_add_to_memory_opt_in(monkeypatch):
    class _AppConfig:
        def get_default_run_config(self):
            return {}

    class _Threads:
        async def create(self):
            return {"thread_id": "child-thread"}

        async def get_state(self, _thread_id):
            return {"values": {"messages": [{"type": "ai", "content": '{"full_address": ""}'}]}}

    class _Runs:
        def __init__(self):
            self.create_kwargs = None

        async def create(self, *args, **kwargs):
            self.create_kwargs = kwargs
            return {"run_id": "child-run"}

        async def join(self, _thread_id, _run_id):
            return None

    class _Client:
        def __init__(self):
            self.threads = _Threads()
            self.runs = _Runs()

    client = _Client()
    active = workflow_router._ActiveWorkflowRun()
    monkeypatch.setattr(workflow_router, "get_app_config", lambda: _AppConfig())
    row = {"row_index": 0, "row_number": "12", "source": {"name": "Example A"}}
    payload = _workflow_payload()
    payload["execution"]["add_to_memory"] = True

    await workflow_router._execute_child_row(client, "parent-thread", payload, row, active)

    assert client.runs.create_kwargs["context"]["add_to_memory"] is True
    assert client.runs.create_kwargs["context"]["compact_title"] == "wf r12"
