"""The remedy attached to an error must fit that error.

Round 3, VKS technical debt 3: ``"Run 'vmware-vks check' to verify
connectivity."`` appeared verbatim on sixteen tool handlers. Two problems, and
the second is the expensive one:

* It is often wrong. On a vCenter without Workload Management,
  ``get_supervisor_status`` returns vCenter's own precise sentence -- "Cluster
  with identifier domain-c9 does not have Workloads enabled" -- and then told
  the reader to check a connection that was fine. ``vmware-vks check`` passes.
* It displaced remedies the skill had already written. ``k8s_connection`` raises
  "No Supervisor cluster is in config_status RUNNING. Run
  check_vks_compatibility ... then get_supervisor_status", and the pasted line
  sat next to it as a second, contradictory next step.

Debt 5 is here too: the Supervisor kubeconfig -- the higher-privileged of the
two -- had no ``output_path``, while its sibling's docstring told callers to
"always prefer output_path so the credential never enters agent context".
"""

from __future__ import annotations

import inspect

from vmware_vks.errors import VksApiError, VksError
from vmware_vks.mcp_server import server as srv


def test_an_authored_error_keeps_its_own_remedy():
    out = srv._tool_error(
        VksApiError(
            "No Supervisor cluster is in config_status RUNNING. Run "
            "check_vks_compatibility, then get_supervisor_status."
        )
    )
    assert "check_vks_compatibility" in out["error"]
    assert "hint" not in out, (
        "a generic connectivity hint was pasted beside a specific remedy — "
        "the agent now has two contradictory next steps"
    )


def test_an_unauthored_error_still_gets_a_next_step():
    out = srv._tool_error(TimeoutError("timed out"))
    assert out["hint"], "an error with no authored remedy must still say what to try"


def test_every_vks_error_subclass_is_treated_as_authored():
    """Guards against a new subclass silently falling into the generic branch."""
    for cls in (VksError, VksApiError):
        assert "hint" not in srv._tool_error(cls("x")), cls.__name__


def test_no_hint_text_is_shared_by_more_than_one_handler():
    """AST, not grep, and the invariant is repetition rather than wording.

    Per-tool hints are the good case -- ``create_namespace`` pointing at
    ``list_supervisor_storage_policies`` is worth having. What went wrong was
    one sentence pasted onto sixteen different tools, which cannot be specific
    to any of them. So the rule is: a literal hint may appear once. The shared
    fallback lives in ``_tool_error``, written once and reached by all of them.

    Grepping the source instead would match this test's own rationale and the
    docstring on ``_tool_error`` -- a check asserting something other than its
    name (形态 #4).
    """
    import ast
    from collections import Counter

    tree = ast.parse(inspect.getsource(srv))
    hints = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "hint"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                hints[" ".join(value.value.split())] += 1
    repeated = {text: n for text, n in hints.items() if n > 1}
    assert not repeated, (
        f"hint text is pasted onto more than one handler: "
        f"{ {t[:60]: n for t, n in repeated.items()} }. A shared remedy belongs "
        f"in one helper, not copied per tool where it goes stale unevenly."
    )


def test_the_supervisor_kubeconfig_can_avoid_agent_context():
    params = inspect.signature(srv.get_supervisor_kubeconfig).parameters
    assert "output_path" in params, (
        "the higher-privileged kubeconfig still has no way out of the transcript, "
        "while its sibling's docstring recommends exactly that"
    )
    assert params["output_path"].default is None
