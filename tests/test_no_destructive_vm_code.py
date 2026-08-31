"""Guard: vmware-vks must never contain pyVmomi VM lifecycle operations."""
import ast
import pathlib

FORBIDDEN_CALLS = {
    "PowerOn", "PowerOff", "Destroy", "Clone", "Relocate",
    "ReconfigVM", "MarkAsVirtualMachine", "RegisterVM",
}


def test_no_vm_lifecycle_ops():
    src_dir = pathlib.Path("vmware_vks")
    for py_file in src_dir.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_CALLS:
                raise AssertionError(
                    f"{py_file}: Found forbidden VM lifecycle call '{node.attr}'. "
                    "vmware-vks must not modify VMs directly."
                )
