"""Kubeconfig retrieval for Supervisor and TKC clusters."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_vks.errors import VksApiError

_log = logging.getLogger("vmware-vks.ops.kubeconfig")


def get_supervisor_kubeconfig_str(si: ServiceInstance, namespace: str) -> str:
    """Get kubeconfig YAML string for Supervisor namespace."""
    from vmware_vks.k8s_connection import get_supervisor_kubeconfig_str as _get
    return _get(si, namespace)


def build_tkc_kubeconfig(
    si: ServiceInstance, cluster_name: str, namespace: str
) -> dict[str, Any]:
    """Build kubeconfig as a dict for a TKC cluster via the Supervisor API.

    Returning a dict (vs. a YAML string) lets in-process callers feed it
    directly into kubernetes.config.load_kube_config_from_dict and keep
    the bearer token in memory.
    """
    import kubernetes as k8s

    from vmware_vks.k8s_connection import get_k8s_client, translate_k8s_error
    from vmware_vks.ops.tkc import _resolve_tkc_version

    version = _resolve_tkc_version(si, namespace)
    api_client = get_k8s_client(si, namespace)
    try:
        custom_api = k8s.client.CustomObjectsApi(api_client)
        try:
            cluster = custom_api.get_namespaced_custom_object(
                group="cluster.x-k8s.io", version=version,
                namespace=namespace, plural="clusters", name=cluster_name,
            )
        except k8s.client.exceptions.ApiException as e:
            raise translate_k8s_error(
                si, e, resource=cluster_name, namespace=namespace
            ) from e

        control_plane_endpoint = cluster.get("spec", {}).get("controlPlaneEndpoint", {})
        host = control_plane_endpoint.get("host", "")
        port = control_plane_endpoint.get("port", 6443)

        if not host:
            raise VksApiError(
                f"TKC cluster '{cluster_name}' in namespace '{namespace}' has no "
                f"control plane endpoint yet — it is not fully provisioned. Run "
                f"get_tkc_cluster to check its phase, and retry once it reports "
                f"Running."
            )

        # Supervisor JWT from POST /wcp/login — the SOAP session key is not
        # a valid K8s bearer token (see vmware_vks.wcp_login).
        from vmware_vks.wcp_login import get_wcp_token

        token = get_wcp_token(si)
        # Honour verify_ssl (see k8s_connection); only lab/self-signed skips TLS.
        from vmware_vks.connection import get_verify_ssl

        skip_tls = not get_verify_ssl(si)
        return {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [{"name": cluster_name, "cluster": {
                "server": f"https://{host}:{port}",
                "insecure-skip-tls-verify": skip_tls,
            }}],
            "users": [{"name": "vsphere-user", "user": {"token": token}}],
            "contexts": [{"name": f"{cluster_name}-context", "context": {
                "cluster": cluster_name, "user": "vsphere-user",
            }}],
            "current-context": f"{cluster_name}-context",
        }
    finally:
        api_client.close()


def get_tkc_kubeconfig_str(si: ServiceInstance, cluster_name: str, namespace: str) -> str:
    """Get kubeconfig YAML string for a TKC cluster via Supervisor API.

    Used when the kubeconfig must be exported to a user-chosen path or
    displayed. For in-process use, prefer build_tkc_kubeconfig.
    """
    import yaml as _yaml
    return _yaml.dump(build_tkc_kubeconfig(si, cluster_name, namespace))


def _write_kubeconfig_file(output_path: Path, content: str) -> Path:
    """Write a token-bearing kubeconfig to ``output_path`` securely.

    The file carries a live Supervisor bearer token, so:
      * refuse a symlink at the target (prevents redirecting the token to an
        attacker-controlled location);
      * write a *new* file — created O_CREAT|O_EXCL, mode 0600, in the target's
        directory — fsync it, then ``os.replace`` it over the target.

    Why not open the target itself: an existing kubeconfig opened with O_TRUNC
    and narrowed with fchmod keeps its inode, and fchmod does not revoke
    descriptors already open on it — whoever had the old 0644 file open read
    the token as it landed. And truncating first meant any later failure
    (fchmod refused, disk full) had already destroyed the user's file. A rename
    leaves old descriptors on the old inode and the target untouched until the
    new content is complete. ``os.replace`` also replaces a symlink rather than
    following it, so a link swapped in after the check cannot redirect the token.

    The user/agent may still choose any directory they have write access to —
    that is the function's purpose; we only block symlink redirection.
    """
    target = Path(output_path).expanduser()

    if target.is_symlink():
        raise ValueError(
            f"Refusing to write kubeconfig through a symlink: {output_path}. "
            f"The file carries a live session token, so following the link could "
            f"redirect it elsewhere. Remove the symlink, or pass a regular file "
            f"path to --output."
        )

    target.parent.mkdir(parents=True, exist_ok=True)

    # mkstemp: O_CREAT|O_EXCL (and O_NOFOLLOW where available), mode 0600.
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
    except OSError as e:
        raise ValueError(
            f"Cannot write kubeconfig to {output_path}: {e}. Check that the "
            f"parent directory exists and is writable, then retry with a "
            f"writable --output path."
        ) from e

    tmp = Path(tmp_name)
    try:
        _write_owner_only(fd, content, output_path)
        os.replace(tmp, target)
    except OSError as e:
        _discard(tmp)
        raise ValueError(
            f"Cannot write kubeconfig to {output_path}: {e}. Any existing "
            f"file there was left unchanged. Check that the directory is "
            f"writable and has free space, then retry."
        ) from e
    except BaseException:
        _discard(tmp)
        raise
    return target


def _write_owner_only(fd: int, content: str, output_path: Path) -> None:
    """Pin ``fd`` to 0600, write ``content``, and fsync it. Always closes ``fd``.

    mkstemp already asks for 0600, but the umask can narrow it further; fchmod
    makes the result exactly owner read/write. On platforms without fchmod
    (Windows) access is governed by the directory's ACL, not mode bits.
    """
    try:
        if hasattr(os, "fchmod"):
            try:
                os.fchmod(fd, 0o600)
            except OSError as e:
                raise ValueError(
                    f"Refusing to write kubeconfig to {output_path}: cannot make "
                    f"it owner-only ({e}), so the token would be readable by "
                    f"others. Pass an --output path in a directory you own."
                ) from e
        fh = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    with fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())


def _discard(tmp: Path) -> None:
    """Remove a half-written temp file; a failure here must not mask the cause."""
    try:
        tmp.unlink(missing_ok=True)
    except OSError as e:
        _log.warning("Could not remove temporary kubeconfig %s: %s", tmp, e)


def write_supervisor_kubeconfig(
    si: ServiceInstance,
    namespace: str,
    output_path: Path | None = None,
) -> dict:
    """Write the Supervisor kubeconfig to a file, or return it as a string.

    The TKC tool has had ``output_path`` from the start, and its docstring tells
    callers to "always prefer output_path so the credential never enters agent
    context". This one had no such parameter -- so the only way to obtain the
    *higher-privileged* of the two credentials was to have it returned into the
    conversation. The advice and the missing parameter were pointing in opposite
    directions.
    """
    content = get_supervisor_kubeconfig_str(si, namespace)
    if output_path:
        written = _write_kubeconfig_file(output_path, content)
        return {"namespace": namespace, "written_to": str(written)}
    return {"namespace": namespace, "kubeconfig": content}


def write_kubeconfig(
    si: ServiceInstance,
    cluster_name: str,
    namespace: str,
    output_path: Path | None = None,
) -> dict:
    """Write TKC kubeconfig to file or return as string."""
    kubeconfig_str = get_tkc_kubeconfig_str(si, cluster_name, namespace)
    if output_path:
        written = _write_kubeconfig_file(output_path, kubeconfig_str)
        return {"cluster": cluster_name, "written_to": str(written)}
    return {"cluster": cluster_name, "kubeconfig": kubeconfig_str}
