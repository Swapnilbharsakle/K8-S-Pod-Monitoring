"""
k8s_health_monitor.py
=====================
Kubernetes Pod Health Monitor — generates a CSV report and HTML email dashboards
for multi-environment, multi-application cluster health checks.

Usage
-----
    python k8s_health_monitor.py <env> <app>

Arguments
---------
    env   Comma-separated list of environments to scan, or "all".
          Valid values: env1, env1-bg, env2, env2-bg, env3, env3-bg
    app   Comma-separated list of application folder names, or "all".

Examples
--------
    python k8s_health_monitor.py all all
    python k8s_health_monitor.py env1,env2 APP_CORE,APP_AUTH
    python k8s_health_monitor.py env1-bg APP_BILLING

Outputs
-------
    pod_health_report_<BUILD_NUMBER>.csv   — flat CSV with per-pod status rows
    email_table.html                       — inline HTML email summary dashboard
    full_failure_report.html               — visual failure drill-down report

Environment Variables
---------------------
    BUILD_NUMBER   CI build identifier injected at runtime (default: "N/A")
    BUILD_URL      Base URL of the CI build, used to construct report links
    KUBECONFIG     Set automatically per cluster config file during execution

Notes
-----
    📝  Markers labelled "NOTE" throughout the file flag lines you must update
        before running against a real infrastructure layout.
"""

import csv
import json
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNIFIED_HEADERS = [
    "Build_Number",
    "Env",
    "App",
    "Namespace",
    "Resource_Type",
    "Resource_Name",
    "HPA",
    "min",
    "max",
    "ready",
    "Current",
    "Status",
]

ALL_ENVS = ["env1", "env1-bg", "env2", "env2-bg", "env3", "env3-bg"]

WORKLOAD_TYPES = ["DEPLOYMENT", "STATEFULSET", "DAEMONSET", "ROLLOUT"]

POD_ERROR_STATES = [
    "ContainerCreating",
    "CrashLoopBackOff",
    "Pending",
    "Evicted",
    "ErrImagePull",
    "ImagePullBackOff",
]

# ---------------------------------------------------------------------------
# Shell helper
# ---------------------------------------------------------------------------


def run_command(cmd: str) -> str:
    """Run a shell command and return its stdout as a string.

    Returns a string prefixed with ``ERROR:`` on failure so callers can do a
    simple ``startswith("ERROR:")`` check without catching exceptions.
    """
    try:
        return subprocess.check_output(
            cmd,
            shell=True,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            timeout=45,
        ).decode("utf-8")
    except Exception as exc:
        raw = ""
        if hasattr(exc, "output") and exc.output:
            raw = exc.output.decode("utf-8").strip()
        return f"ERROR: {raw or str(exc)}"


# ---------------------------------------------------------------------------
# Cluster config helpers
# ---------------------------------------------------------------------------


def extract_global_token(config_path: str) -> str | None:
    """Extract a bearer token from a kubeconfig file, if present."""
    try:
        with open(config_path, "r") as fh:
            content = fh.read()
        match = re.search(
            r"token:\s*(ey[A-Za-z0-9_=\-]+\.[A-Za-z0-9_=\-]+\.[A-Za-z0-9_=\-]+)",
            content,
        )
        return match.group(1).strip() if match else None
    except Exception:
        return None


def resolve_absolute_cluster_matrix(app: str, env: str) -> tuple[list, list]:
    """Return (config_file_names, namespaces) for the given app/env pair.

    📝 NOTE: Replace "sample-app-code" with your baseline app identifier.
    📝 NOTE: Update the ``files`` mapping to fit your internal infrastructure
             file layout and add additional ``if/elif`` branches for other apps.
    """
    clean_env = env.replace("-bg", "").lower().strip()
    is_bg = "-bg" in env.lower()
    suffix = "blue" if is_bg else "green"
    tier = clean_env.replace("env", "")

    if app.lower().split("-")[0].strip() == "sample-app-code":
        files = {
            "env1": [f"cluster-matrix-t1-{suffix}-app.config"],
            "env2": [
                f"cluster-matrix-cxp-{'blue' if is_bg else 'green'}-app.config"
            ],
            "env3": [
                f"cluster-matrix-t3-{'blue' if is_bg else 'green'}-app.config"
            ],
        }
        return files.get(clean_env, []), [f"namespace-env{tier}-apps"]

    return [], []


# ---------------------------------------------------------------------------
# Core health-check logic
# ---------------------------------------------------------------------------


def get_health(env_input: str, app_input: str) -> None:
    """Collect Kubernetes health data and write CSV + HTML reports.

    Parameters
    ----------
    env_input:
        Comma-separated environment names or ``"all"``.
    app_input:
        Comma-separated application folder names or ``"all"``.
    """
    build_num = os.environ.get("BUILD_NUMBER", "N/A")
    env_raw = env_input.strip().lower()
    app_raw = app_input.strip().lower()

    # Resolve target environments
    if "all" in env_raw:
        target_envs = ALL_ENVS
    else:
        target_envs = [
            e.strip() for e in env_raw.split(",") if e.strip() in ALL_ENVS
        ]

    # 📝 NOTE: Update this base directory path to match your repo layout.
    base_dir = os.path.join("..", "kubeconfigs", "APPLICATIONS")

    # Resolve target applications
    if "all" in app_raw and os.path.exists(base_dir):
        target_apps = [
            d
            for d in os.listdir(base_dir)
            if (
                os.path.isdir(os.path.join(base_dir, d))
                and not d.startswith(".")
                and os.path.exists(os.path.join(base_dir, d, "NONPROD", "region-id"))
            )
        ]
    else:
        target_apps = list(
            dict.fromkeys(
                [
                    a.strip().replace("-gz", "").replace("-yz", "").upper()
                    for a in app_raw.split(",")
                    if a.strip()
                ]
            )
        )

    if not target_apps:
        # 📝 NOTE: Replace with your own fallback application portfolio.
        target_apps = ["APP_CORE", "APP_AUTH", "APP_GATEWAY", "APP_BILLING", "APP_DATA"]

    final_rows: list[dict] = []

    for folder in target_apps:
        app_clean = folder.lower().split("-")[0].strip()
        disp_app = folder.upper() if app_clean != "app_billing" else app_clean.upper()

        for env in target_envs:
            config_files, namespaces = resolve_absolute_cluster_matrix(folder, env)
            if not config_files:
                continue

            is_bg = "-bg" in env
            disp_env = f"{env.replace('-bg', '').upper()} {'Blue' if is_bg else 'Green'}"

            for cfg in config_files:
                # 📝 NOTE: Ensure this sub-path matches your directory structure.
                cfg_path = os.path.join(
                    base_dir, folder, "NONPROD", "region-id", cfg
                )

                if not os.path.exists(cfg_path):
                    final_rows.append(
                        _make_row(
                            build_num, disp_env, disp_app,
                            namespace="N/A",
                            resource_type="CONFIG_FILE",
                            resource_name=cfg,
                            status="CRITICAL (Config file missing)",
                        )
                    )
                    continue

                os.environ["KUBECONFIG"] = cfg_path
                tok = extract_global_token(cfg_path)
                t_flag = f"--token={tok}" if tok else ""

                ctx_output = run_command("kubectl config get-contexts -o name")
                if ctx_output.startswith("ERROR:") or not ctx_output.strip():
                    final_rows.append(
                        _make_row(
                            build_num, disp_env, disp_app,
                            namespace="N/A",
                            resource_type="CLUSTER",
                            resource_name="N/A",
                            status=f"CRITICAL ({ctx_output.replace('ERROR:', '').strip()[:50]})",
                        )
                    )
                    continue

                active_ctx = ctx_output.splitlines()[0].strip()
                run_command(f"kubectl config use-context {active_ctx}")

                for ns in namespaces:
                    _collect_namespace_rows(
                        final_rows, build_num, disp_env, disp_app, ns, t_flag
                    )

    # Write CSV
    report_name = f"pod_health_report_{build_num}.csv"
    with open(report_name, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNIFIED_HEADERS)
        writer.writeheader()
        if final_rows:
            writer.writerows(final_rows)

    generate_split_dashboard_email(final_rows, target_envs)


def _collect_namespace_rows(
    final_rows: list,
    build_num: str,
    disp_env: str,
    disp_app: str,
    ns: str,
    t_flag: str,
) -> None:
    """Gather workload and pod rows for a single namespace."""
    row_count_before = len(final_rows)
    hpa_map: dict = {}
    conn_err: str | None = None

    # ---- HPA data --------------------------------------------------------
    hpa_raw = run_command(f"kubectl get hpa -n {ns} {t_flag} -o json")
    if hpa_raw.startswith("ERROR:"):
        if any(x in hpa_raw.lower() for x in ["unauthorized", "timeout", "connect"]):
            conn_err = hpa_raw
    else:
        for h in json.loads(hpa_raw).get("items", []):
            target = h["spec"]["scaleTargetRef"]["name"].lower()
            hpa_map[target] = {
                "min": h["spec"].get("minReplicas", 1),
                "max": h["spec"].get("maxReplicas", 1),
            }

    # ---- Workload data ---------------------------------------------------
    workloads: dict = {}
    for rt in ["deployment", "statefulset", "daemonset", "rollout"]:
        wl_raw = run_command(f"kubectl get {rt} -n {ns} {t_flag} -o json")
        if wl_raw.startswith("ERROR:") or "items" not in wl_raw:
            continue
        for item in json.loads(wl_raw).get("items", []):
            name = item["metadata"]["name"]
            replicas = item.get("spec", {}).get("replicas", 1) or 1
            h_info = hpa_map.get(name.lower(), {"min": replicas, "max": replicas})
            workloads[name.lower()] = {
                "display_name": name,
                "type": rt.upper(),
                "hpa": "Active" if name.lower() in hpa_map else "Missing",
                "min": h_info["min"],
                "max": h_info["max"],
                "pods": [],
            }

    # ---- Pod data --------------------------------------------------------
    pod_raw = run_command(f"kubectl get pods -n {ns} {t_flag} -o json")
    if not pod_raw.startswith("ERROR:") and "items" in pod_raw:
        for pod in json.loads(pod_raw).get("items", []):
            p_name = pod["metadata"]["name"]
            status_block = pod.get("status", {})
            phase = status_block.get("phase", "Unknown")
            base_name = re.sub(r"-[a-z0-9]+-[a-z0-9]+$", "", p_name).lower()

            c_statuses = status_block.get("containerStatuses", [])
            total_containers = len(c_statuses) if c_statuses else 1
            ready_containers = sum(1 for c in c_statuses if c.get("ready", False))
            is_healthy = int(phase == "Running" and ready_containers == total_containers)

            # Derive a human-readable pod status
            pod_status = phase
            if ready_containers < total_containers and c_statuses:
                pod_status = status_block.get("reason", phase)
                for c in c_statuses:
                    if not c.get("ready", False):
                        state = c.get("state", {})
                        pod_status = (
                            state.get("waiting", {}).get("reason")
                            or state.get("terminated", {}).get("reason")
                            or pod_status
                        )
                        break

            if base_name in workloads:
                workloads[base_name]["pods"].append(
                    {
                        "name": p_name,
                        "ready": f"{ready_containers}/{total_containers}",
                        "status": pod_status,
                        "healthy": is_healthy,
                    }
                )

    # ---- Aggregate into rows ---------------------------------------------
    for w_key, w_meta in workloads.items():
        total_pods = len(w_meta["pods"])
        healthy_pods = sum(p["healthy"] for p in w_meta["pods"])
        min_replicas = int(w_meta["min"])

        if healthy_pods >= min_replicas:
            w_status = "PASS"
        elif healthy_pods == 0:
            w_status = f"CRITICAL (Workload Dropped to 0: {healthy_pods}/{min_replicas})"
        else:
            w_status = f"CRITICAL (HPA Under-Provisioned: {healthy_pods}/{min_replicas})"

        hpa_label = "Active" if w_key in hpa_map else w_meta["hpa"]

        final_rows.append(
            {
                "Build_Number": build_num,
                "Env": disp_env,
                "App": disp_app,
                "Namespace": ns,
                "Resource_Type": w_meta["type"],
                "Resource_Name": w_meta["display_name"],
                "HPA": hpa_label,
                "min": w_meta["min"],
                "max": w_meta["max"],
                "ready": f"{healthy_pods}/{total_pods}",
                "Current": healthy_pods,
                "Status": w_status,
            }
        )

        for pod in w_meta["pods"]:
            final_rows.append(
                {
                    "Build_Number": build_num,
                    "Env": disp_env,
                    "App": disp_app,
                    "Namespace": ns,
                    "Resource_Type": "POD_INSTANCE",
                    "Resource_Name": pod["name"],
                    "HPA": "N/A",
                    "min": "-",
                    "max": "-",
                    "ready": pod["ready"],
                    "Current": pod["healthy"],
                    "Status": pod["status"],
                }
            )

    # ---- Fallback row when nothing was found -----------------------------
    if len(final_rows) == row_count_before:
        if conn_err:
            err_snippet = conn_err.replace("ERROR:", "").strip()[:50]
            fallback_status = f"CRITICAL (Connect Failure: {err_snippet})"
        else:
            fallback_status = "PASS (No active workloads found)"

        final_rows.append(
            _make_row(
                build_num, disp_env, disp_app,
                namespace=ns,
                resource_type="NAMESPACE",
                resource_name="N/A",
                status=fallback_status,
            )
        )


def _make_row(
    build_num: str,
    env: str,
    app: str,
    *,
    namespace: str,
    resource_type: str,
    resource_name: str,
    status: str,
) -> dict:
    """Return a skeleton row dict with sensible defaults for non-pod rows."""
    return {
        "Build_Number": build_num,
        "Env": env,
        "App": app,
        "Namespace": namespace,
        "Resource_Type": resource_type,
        "Resource_Name": resource_name,
        "HPA": "N/A",
        "min": "-",
        "max": "-",
        "ready": "0/0",
        "Current": 0,
        "Status": status,
    }


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------


def generate_split_dashboard_email(
    flat_data: list[dict], target_envs: list[str] = ()
) -> str:
    """Build two HTML reports from ``flat_data`` and write them to disk.

    Files written
    -------------
    email_table.html         — compact email-friendly summary tables
    full_failure_report.html — visual per-pod failure drill-down

    Returns
    -------
    str  The generated email HTML string.
    """
    build_num = os.environ.get("BUILD_NUMBER", "N/A")
    unique_apps = {r["App"] for r in flat_data if r["App"] != "N/A"}

    # ---- Per-app summary counts ------------------------------------------
    vsad: dict = {}
    for r in flat_data:
        app = r["App"]
        if app == "N/A" or r["Resource_Type"] in ["CONFIG_FILE", "CLUSTER"]:
            continue
        vsad.setdefault(
            app,
            {
                "dep_total": 0, "dep_success": 0, "dep_failure": 0,
                "pod_total": 0, "pod_success": 0, "pod_failure": 0,
            },
        )
        if r["Resource_Type"] in WORKLOAD_TYPES:
            vsad[app]["dep_total"] += 1
            key = "dep_success" if "PASS" in str(r["Status"]) else "dep_failure"
            vsad[app][key] += 1
        elif r["Resource_Type"] == "POD_INSTANCE":
            vsad[app]["pod_total"] += 1
            is_ok = r["Status"] == "Running" and not str(r["ready"]).startswith("0/")
            vsad[app]["pod_success" if is_ok else "pod_failure"] += 1

    # ---- Per-env error matrix -------------------------------------------
    # 📝 NOTE: Ensure these keys match your environment naming conventions.
    matrix: dict = {t: {} for t in ["ENV1", "ENV2", "ENV3"]}
    error_keys = POD_ERROR_STATES + ["resource_mismatch", "critical_hpa", "has_errors"]

    for r in flat_data:
        env, app = r.get("Env", "N/A"), r.get("App", "N/A")
        if env == "N/A" or not app or app == "N/A":
            continue
        base_env = env.split()[0].upper()
        if base_env not in matrix:
            matrix[base_env] = {}

        m = matrix[base_env].setdefault(app, {k: 0 for k in error_keys})
        status_str = str(r["Status"])

        if "PASS" not in status_str and "Running" not in status_str:
            m["has_errors"] = True
            if r["Resource_Type"] == "POD_INSTANCE":
                matched = False
                for err in POD_ERROR_STATES:
                    if err in status_str:
                        m[err] += 1
                        matched = True
                        break
                if not matched:
                    m["resource_mismatch"] += 1
            elif "CRITICAL" in status_str:
                m["critical_hpa"] += 1

    # ---- Build email HTML ------------------------------------------------
    css = (
        "table{border-collapse:collapse;width:100%;font-family:Calibri;margin-bottom:25px}"
        "th{background-color:#333;color:white;padding:10px}"
        "td{border:1px solid #ddd;padding:8px;text-align:center}"
        ".crit{background-color:#f2dede;color:#a94442;font-weight:bold}"
        "h3{font-family:Calibri;color:#222}"
        ".env-header{font-family:Calibri;font-size:16px;font-weight:bold;"
        "margin-top:15px;margin-bottom:8px;color:#c00}"
    )
    html = f"<html><head><style>{css}</style></head><body>"

    # Summary counts table
    total_workloads = sum(1 for r in flat_data if r["Resource_Type"] in WORKLOAD_TYPES)
    total_pods = sum(1 for r in flat_data if r["Resource_Type"] == "POD_INSTANCE")
    html += (
        "<h3>OVERALL RESOURCE SUMMARY COUNT</h3>"
        "<table><tr>"
        "<th>Total Unique Applications</th>"
        "<th>Total Workload Instances</th>"
        "<th>Total Active Pod Instances</th>"
        "</tr><tr>"
        f"<td>{len(unique_apps)}</td>"
        f"<td>{total_workloads}</td>"
        f"<td>{total_pods}</td>"
        "</tr></table>"
    )

    # Health summary table
    html += (
        "<h3>OVERALL HEALTH STATUS SUMMARY</h3>"
        "<table><tr>"
        "<th>Application Profile</th>"
        "<th>Total Workloads</th><th>Workloads Passing</th><th>Workloads Failing</th>"
        "<th>Total Pods</th><th>Pods Running</th><th>Pods Terminating/Failing</th>"
        "</tr>"
    )
    for app in sorted(vsad):
        d = vsad[app]
        html += (
            f"<tr><td>{app}</td>"
            f"<td>{d['dep_total']}</td><td>{d['dep_success']}</td><td>{d['dep_failure']}</td>"
            f"<td>{d['pod_total']}</td><td>{d['pod_success']}</td><td>{d['pod_failure']}</td>"
            "</tr>"
        )
    html += "</table><h3>TRACKING MATRIX FOR ERRORS ONLY</h3>"

    # Error matrix tables per environment
    # 📝 NOTE: Matches loop target keys to your environment tiers.
    active_tiers = {e.replace("-bg", "").upper() for e in target_envs}
    printed_any = False
    for tier in ["ENV1", "ENV2", "ENV3"]:
        tier_matrix = matrix.get(tier, {})
        if tier not in active_tiers or not any(
            m["has_errors"] for m in tier_matrix.values()
        ):
            continue
        printed_any = True
        header_cells = "".join(
            f"<th>{e} Count</th>" for e in POD_ERROR_STATES
        )
        html += (
            f"<div class='env-header'>📍 Environmental Context Tier: {tier}</div>"
            f"<table><tr><th>Application Profile</th>{header_cells}"
            "<th>Total Critical HPA Exceptions</th></tr>"
        )
        for app in sorted(tier_matrix):
            m = tier_matrix[app]
            if not m["has_errors"]:
                continue
            cells = "".join(
                f"<td {'class=\"crit\"' if m[e] > 0 else ''}>{m[e]}</td>"
                for e in POD_ERROR_STATES
            )
            hpa_cell = (
                f"<td {'class=\"crit\"' if m['critical_hpa'] > 0 else ''}>"
                f"{m['critical_hpa']}</td>"
            )
            html += f"<tr><td style='font-weight:bold'>{app}</td>{cells}{hpa_cell}</tr>"
        html += "</table>"

    if not printed_any:
        html += "<p>All execution tracking workloads are healthy across scanned tracks.</p>"

    # HPA alerts table
    hpa_failures: dict = {}
    for r in flat_data:
        if (
            r["Resource_Type"] in WORKLOAD_TYPES
            and ("PASS" not in str(r["Status"]) or r["HPA"] == "Missing")
            and r["App"] != "N/A"
        ):
            hpa_failures[r["App"]] = hpa_failures.get(r["App"], 0) + 1

    html += "<h3>HORIZONTAL POD AUTOSCALER ALERTS</h3>"
    if hpa_failures:
        rows_html = "".join(
            f"<tr class='crit'><td>{a}</td><td>{c}</td><td>CRITICAL</td></tr>"
            for a, c in sorted(hpa_failures.items())
        )
        html += (
            "<table><tr>"
            "<th>Application Profile</th>"
            "<th>Total Critical HPA Counts</th>"
            "<th>Operational Level</th>"
            f"</tr>{rows_html}</table>"
        )
    else:
        html += "<p>All monitored workloads contain functional autoscaling properties.</p>"

    # ---- Build visual failure drill-down report --------------------------
    v_matrix: dict = {}
    for r in flat_data:
        status_str = str(r["Status"])
        if (
            "PASS" not in status_str
            and "Running" not in status_str
            and r["App"] != "N/A"
            and r["Env"] != "N/A"
        ):
            base_env = r["Env"].split()[0].upper()
            v_matrix.setdefault(base_env, {}).setdefault(r["App"], []).append(r)

    failure_css = (
        "body{font-family:'Segoe UI',sans-serif;margin:30px;background-color:#f4f6f9}"
        ".container{max-width:1100px;margin:0 auto;background:white;padding:25px;border-radius:8px}"
        "h1{color:#d9534f;border-bottom:3px solid #d9534f}"
        "h2{color:#2c3e50;border-left:5px solid #2c3e50;padding-left:10px}"
        "h3{background-color:#f8f9fa;padding:8px;border-left:4px solid #d9534f}"
        "table{border-collapse:collapse;width:100%;margin-bottom:25px}"
        "th{background-color:#343a40;color:white;padding:12px;font-size:13px}"
        "td{border:1px solid #dee2e6;padding:10px;font-size:13px}"
        ".crit-row{background-color:#fff5f5}"
        ".badge{background-color:#d9534f;color:white;padding:3px 8px;"
        "border-radius:4px;font-size:10px}"
        ".healthy-msg{color:#28a745;background:#f4fff6;padding:10px;"
        "border:1px solid #d4edda}"
    )
    v_ui = (
        f"<html><head><style>{failure_css}</style></head><body>"
        "<div class='container'>"
        f"<h1>🚨 Infrastructure Component Exception Diagnostic Log [Build #{build_num}]</h1>"
    )

    has_pod_failures = any(
        any(item["Resource_Type"] == "POD_INSTANCE" for item in items)
        for tier_data in v_matrix.values()
        for items in tier_data.values()
    )

    if has_pod_failures:
        for tier in ["ENV1", "ENV2", "ENV3"]:
            tier_data = v_matrix.get(tier, {})
            if tier not in active_tiers:
                continue
            pod_apps = {
                app: items
                for app, items in tier_data.items()
                if any(i["Resource_Type"] == "POD_INSTANCE" for i in items)
            }
            if not pod_apps:
                continue
            v_ui += f"<h2>🌍 OPERATIONAL BOUNDARY TRACK: {tier}</h2>"
            for app in sorted(pod_apps):
                failing_pods = [
                    i for i in pod_apps[app] if i["Resource_Type"] == "POD_INSTANCE"
                ]
                if not failing_pods:
                    continue
                v_ui += (
                    f"<h3>📦 Identity Context Profile: {app}</h3>"
                    "<table><tr>"
                    "<th>Failing Pod Instance Name</th>"
                    "<th>Cluster Context</th>"
                    "<th>Resource Type</th>"
                    "<th>Ready State</th>"
                    "<th>Exact Container Error</th>"
                    "</tr>"
                )
                for item in failing_pods:
                    v_ui += (
                        "<tr class='crit-row'>"
                        f"<td><b>{item.get('Resource_Name', 'N/A')}</b></td>"
                        f"<td>{item.get('Env', 'N/A')}</td>"
                        f"<td><span class='badge'>{item.get('Resource_Type', 'N/A')}</span></td>"
                        f"<td style='text-align:center'>{item.get('ready', 'N/A')}</td>"
                        f"<td style='color:#b94a48'>{item.get('Status', 'N/A')}</td>"
                        "</tr>"
                    )
                v_ui += "</table>"
    else:
        v_ui += (
            "<div class='healthy-msg'>"
            "✔ All target systems passing checks completely across checked runtime matrices."
            "</div>"
        )

    with open("full_failure_report.html", "w") as fh:
        fh.write(v_ui + "</div></body></html>")

    # ---- Append link to visual report in email ---------------------------
    build_url = os.environ.get("BUILD_URL", "")
    # 📝 NOTE: If using Jenkins, ensure 'Infrastructure_Dashboard' maps accurately.
    if build_url:
        report_url = f"{build_url}Infrastructure_Dashboard/full_failure_report.html"
    else:
        report_url = "full_failure_report.html"

    html += (
        "<br><hr>"
        "<p style='font-family:Calibri;font-size:15px'>"
        "💡 <b>Need to review exact item contexts, crashing containers, "
        "or mismatched resource allocations?</b><br>"
        f"<a href='{report_url}' target='_blank' style='"
        "display:inline-block;margin-top:8px;padding:10px 18px;"
        "background-color:#d9534f;color:white;text-decoration:none;"
        "font-weight:bold;border-radius:4px'>"
        "👉 CLICK HERE TO VIEW VISUAL FAILURE REPORT 👈"
        "</a></p>"
        "</body></html>"
    )

    with open("email_table.html", "w") as fh:
        fh.write(html)

    return html


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        get_health(sys.argv[1], sys.argv[2])
    else:
        print(
            "Usage: python k8s_health_monitor.py <env> <app>\n"
            "  env  — comma-separated list or 'all'\n"
            "  app  — comma-separated list or 'all'\n"
        )
        sys.exit(1)
