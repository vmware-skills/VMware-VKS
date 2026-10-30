"""The 9.1 advisory report must fail closed when evidence is missing."""

from unittest.mock import MagicMock, patch

import pytest

from vmware_vks.ops.upgrade_risk import get_supervisor_upgrade_risk


def _si(version: str) -> MagicMock:
    si = MagicMock()
    si.content.about.version = version
    si.content.about.build = "12345678"
    return si


@pytest.mark.parametrize("version", ["8.0.3", "8.0.4", "9.0.0", "9.0.2"])
def test_affected_source_with_supervisor_requires_official_check(version):
    with patch("vmware_vks.ops.upgrade_risk._rest_get", return_value=[
        {"cluster": "domain-c1", "config_status": "RUNNING"}
    ]):
        report = get_supervisor_upgrade_risk(_si(version))
    assert report["advisory_applicability"] == "in_scope"
    assert report["vumdb_schema_status"] == "unmeasured"
    assert report["upgrade_clearance"] == "not_established"
    assert "455737" in report["references"]["official_checker"]


def test_unreadable_supervisor_inventory_is_unknown_not_absent():
    with patch("vmware_vks.ops.upgrade_risk._rest_get", side_effect=RuntimeError("denied")):
        report = get_supervisor_upgrade_risk(_si("8.0.3"))
    assert report["supervisor_presence"] == "unknown"
    assert report["advisory_applicability"] == "unknown"
    assert report["supervisors"] is None


def test_no_supervisor_detected_never_grants_clearance():
    with patch("vmware_vks.ops.upgrade_risk._rest_get", return_value=[]):
        report = get_supervisor_upgrade_risk(_si("9.0.1"))
    assert report["advisory_applicability"] == "no_supervisor_detected"
    assert report["upgrade_clearance"] == "not_established"


@pytest.mark.parametrize("version,scope", [
    ("8.0.2", "out_of_scope"), ("9.1.0", "out_of_scope"),
    ("9.0.3", "unknown"), ("unknown", "unknown"),
])
def test_source_version_matrix_is_conservative(version, scope):
    with patch("vmware_vks.ops.upgrade_risk._rest_get", return_value=[]):
        report = get_supervisor_upgrade_risk(_si(version))
    assert report["advisory_applicability"] == scope
    assert report["upgrade_clearance"] == "not_established"


def test_unsupported_target_is_rejected_before_remote_read():
    with pytest.raises(ValueError, match="9.1.0 or 9.1.1"):
        get_supervisor_upgrade_risk(_si("8.0.3"), "9.2.0")
