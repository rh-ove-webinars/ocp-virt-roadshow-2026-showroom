#!/usr/bin/env python3
"""Instructor-run live progress dashboard for Module 1 + Module 8, across one or more
lab clusters.

Not part of the lab guide, and not Ansible -- this is a short-lived, read-only polling
tool meant to be run in a terminal during the live session, not committed cluster state.

Two signals, both required by default, define "this attendee has finished Module 1 and
Module 8":
  - Module 1: a VirtualMachineInstanceMigration for VM "fedora01" with status.phase
    Succeeded exists somewhere in vmexamples-<user> -- the last interactive step of
    Module 1 before its Summary.
  - Module 8: a successful AAP Job named "Patch My VM" has run for that attendee's
    Organization -- the last interactive step of Module 8 before Verify/Cleanup.

Both are things the platforms record on their own; nothing here depends on attendees
self-reporting or on guessing from current VM power state (ambiguous mid-lab).

Usage:
    ansible-galaxy collection install kubernetes.core   # if not already installed
    pip install kubernetes requests pyyaml              # if not already installed

    oc login <cluster1-api> ... && oc config rename-context $(oc config current-context) cluster1
    oc login <cluster2-api> ... && oc config rename-context $(oc config current-context) cluster2

    cp progress-clusters.yml.example .generated/progress-clusters.yml
    # edit .generated/progress-clusters.yml: kubecontext name per cluster, aap_host and
    # aap_admin_password from each cluster's own install-aap.yml output / its own
    # .generated/controller-connection.yml (it gets overwritten per cluster, so copy the
    # values out before running install-aap.yml again against the next cluster).

    ./check-progress.py                        # loops, refreshing every 30s
    ./check-progress.py --once                 # single pass, then exit
    ./check-progress.py --interval 15 --target 60
"""
import argparse
import sys
import time
from pathlib import Path

import requests
import urllib3
import yaml
from kubernetes import client, config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_CONFIG = Path(__file__).parent / ".generated" / "progress-clusters.yml"


def migrated_users(kubecontext, user_prefix):
    """Usernames with a Succeeded VirtualMachineInstanceMigration for fedora01."""
    api_client = config.new_client_from_config(context=kubecontext)
    custom = client.CustomObjectsApi(api_client)
    migrations = custom.list_cluster_custom_object(
        group="kubevirt.io", version="v1", plural="virtualmachineinstancemigrations"
    )
    namespace_prefix = f"vmexamples-{user_prefix}"
    done = set()
    for mig in migrations.get("items", []):
        ns = mig["metadata"]["namespace"]
        vmi_name = mig.get("spec", {}).get("vmiName", "")
        phase = mig.get("status", {}).get("phase", "")
        if ns.startswith(namespace_prefix) and vmi_name == "fedora01" and phase == "Succeeded":
            done.add(ns[len("vmexamples-"):])
    return done


def patched_users(aap_host, aap_admin_password, aap_admin_username="admin"):
    """Usernames (= Organization names) with a successful 'Patch My VM' job."""
    done = set()
    url = f"{aap_host}/api/controller/v2/jobs/"
    params = {"name": "Patch My VM", "status": "successful", "page_size": 200}
    while url:
        resp = requests.get(
            url,
            params=params,
            auth=(aap_admin_username, aap_admin_password),
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        for job in body.get("results", []):
            org_name = job.get("summary_fields", {}).get("organization", {}).get("name")
            if org_name:
                done.add(org_name)
        url = body.get("next")
        params = None  # 'next' is already a full URL with query params included
    return done


def cluster_progress(cluster_cfg, user_prefix, user_count):
    expected = {f"{user_prefix}{n}" for n in range(1, user_count + 1)}
    migrated = migrated_users(cluster_cfg["kubecontext"], user_prefix) & expected
    patched = patched_users(cluster_cfg["aap_host"], cluster_cfg["aap_admin_password"]) & expected
    done = migrated & patched
    return done, expected


def render(cfg, target_pct):
    user_prefix = cfg.get("lab_user_prefix", "user")
    user_count = cfg.get("lab_user_count", 20)

    all_done, all_expected = set(), set()
    lines = []
    for cluster_cfg in cfg["clusters"]:
        name = cluster_cfg["name"]
        try:
            done, expected = cluster_progress(cluster_cfg, user_prefix, user_count)
        except Exception as exc:  # noqa: BLE001 -- surface any single cluster's failure, keep going
            lines.append(f"  {name:<12} ERROR: {exc}")
            continue
        pct = (len(done) / len(expected) * 100) if expected else 0
        lines.append(f"  {name:<12} {len(done):>2}/{len(expected):<2} done  ({pct:5.1f}%)  -- {', '.join(sorted(done)) or '(none yet)'}")
        all_done |= done
        all_expected |= expected

    combined_pct = (len(all_done) / len(all_expected) * 100) if all_expected else 0
    print(f"\n[{time.strftime('%H:%M:%S')}] Module 1 + Module 8 completion")
    print("\n".join(lines))
    print(f"  {'COMBINED':<12} {len(all_done):>2}/{len(all_expected):<2} done  ({combined_pct:5.1f}%)")
    if combined_pct >= target_pct:
        print(f"\n  >>> {combined_pct:.0f}% >= target {target_pct}% -- ready to run the demo capsule <<<")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=f"default: {DEFAULT_CONFIG}")
    parser.add_argument("--interval", type=int, default=30, help="seconds between refreshes (default: 30)")
    parser.add_argument("--target", type=float, default=55.0, help="combined %% to call out as demo-ready (default: 55)")
    parser.add_argument("--once", action="store_true", help="print one pass and exit, instead of looping")
    args = parser.parse_args()

    if not args.config.exists():
        sys.exit(
            f"Config not found: {args.config}\n"
            f"Copy progress-clusters.yml.example there and fill in your clusters first."
        )
    cfg = yaml.safe_load(args.config.read_text())

    try:
        while True:
            render(cfg, args.target)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
