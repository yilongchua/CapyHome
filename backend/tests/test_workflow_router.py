import csv

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
            "current_row_index": 0,
            "completed_rows": 0,
            "consecutive_failures": 0,
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
