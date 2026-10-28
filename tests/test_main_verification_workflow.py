"""Keep automatic verification separate from privileged dependency control."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_main_verification_uses_trusted_event_sha_and_keeps_pr_gates() -> None:
    workflow = (ROOT / ".github/workflows/validate.yml").read_text()
    assert "types: [dependency-main-verification]" in workflow
    assert 'cron: "17 3 * * *"' in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "contents: read" in workflow
    assert ": write" not in workflow
    assert "github.event.workflow_run.head_sha" not in workflow
    assert (
        "github.event_name != 'schedule' && github.event_name != 'repository_dispatch'"
        in workflow
    )
    assert 'run: echo "required=true"' in workflow
    assert "node --test .github/scripts/*.test.mjs" in workflow
    assert workflow.count("if: needs.evidence.outputs.required == 'true'") == 5


def test_reconciliation_never_executes_triggering_pr_code() -> None:
    workflow = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
    assert "types: [dependency-main-verification]" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "contents: read" in workflow
    assert ": write" not in workflow
