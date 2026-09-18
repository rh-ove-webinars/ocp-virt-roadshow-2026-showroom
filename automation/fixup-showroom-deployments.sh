#!/usr/bin/env bash
# Patches the 20 showroom-userN Deployments that RHDP's own provisioning creates -- these
# are NOT defined anywhere in this repo (no manifest/Helm chart for them here), so nothing
# else in automation/ or content/ can capture these fixes. Re-run this after every fresh
# cluster provision, AFTER install-aap.yml and configure-controller.yml have both completed
# (it reads the AAP route and the generated SSH public key those produce).
#
# What it does, per showroom-userN namespace:
#   1. Repoints the "content" container's GIT_REPO_URL/GIT_REPO_REF at this fork's main
#      branch (RHDP's default points at the upstream rhpds repo on a pinned tag).
#   2. Switches the Deployment to strategy.type=Recreate. The default RollingUpdate
#      deadlocks: the pod mounts an RWO PVC (terminal-lab-user-home), and if the new pod
#      lands on a different node than the old one, it hits "Multi-Attach error" forever
#      because RWO volumes can only attach to one node at a time. Recreate kills the old
#      pod (releasing the volume) before creating the new one, avoiding this entirely.
#   3. Adds aap_console_url/aap_ssh_pubkey to each showroom-userdata ConfigMap's
#      user_data.yml, alongside the user/password/openshift_console_url keys RHDP already
#      puts there -- this is how Antora attributes referenced in Module 8 get their values.
#   4. Restarts each Deployment so the content container rebuilds with all of the above.
#
# Usage:
#   oc login <cluster-api-url> -u kubeadmin ...
#   automation/fixup-showroom-deployments.sh
#
# Env vars (all optional, matching automation/group_vars/all.yml's defaults):
#   LAB_USER_COUNT      (default: 20)
#   AAP_NAMESPACE        (default: aap)
#   GIT_REPO_URL         (default: this fork's URL)
#   GIT_REPO_REF         (default: main)
set -euo pipefail

LAB_USER_COUNT="${LAB_USER_COUNT:-20}"
AAP_NAMESPACE="${AAP_NAMESPACE:-aap}"
GIT_REPO_URL="${GIT_REPO_URL:-https://github.com/rh-ove-webinars/ocp-virt-roadshow-2026-showroom.git}"
GIT_REPO_REF="${GIT_REPO_REF:-main}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_PUBKEY_FILE="${SCRIPT_DIR}/.generated/aap_lab_id_rsa.pub"

if [ ! -f "${SSH_PUBKEY_FILE}" ]; then
  echo "ERROR: ${SSH_PUBKEY_FILE} not found -- run configure-controller.yml first." >&2
  exit 1
fi
AAP_SSH_PUBKEY="$(cat "${SSH_PUBKEY_FILE}")"

AAP_ROUTE_HOST="$(oc get route aap -n "${AAP_NAMESPACE}" -o jsonpath='{.spec.host}' 2>/dev/null || true)"
if [ -z "${AAP_ROUTE_HOST}" ]; then
  echo "ERROR: could not find the 'aap' route in namespace ${AAP_NAMESPACE} -- run install-aap.yml first." >&2
  exit 1
fi
AAP_CONSOLE_URL="https://${AAP_ROUTE_HOST}"

echo "GIT_REPO_URL=${GIT_REPO_URL} (ref: ${GIT_REPO_REF})"
echo "AAP_CONSOLE_URL=${AAP_CONSOLE_URL}"
echo "AAP_SSH_PUBKEY=${AAP_SSH_PUBKEY}"
echo

for i in $(seq 1 "${LAB_USER_COUNT}"); do
  ns="showroom-user${i}"
  echo "=== ${ns} ==="

  oc set env deployment/showroom -c content \
    "GIT_REPO_URL=${GIT_REPO_URL}" "GIT_REPO_REF=${GIT_REPO_REF}" \
    -n "${ns}"

  oc patch deployment showroom -n "${ns}" --type=merge \
    -p '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}'

  current_data="$(oc get cm showroom-userdata -n "${ns}" -o jsonpath='{.data.user_data\.yml}')"
  filtered_data="$(echo "${current_data}" | grep -v '^"aap_console_url"' | grep -v '^"aap_ssh_pubkey"')"
  new_data="$(printf '%s\n"aap_console_url": "%s"\n"aap_ssh_pubkey": "%s"\n' \
    "${filtered_data}" "${AAP_CONSOLE_URL}" "${AAP_SSH_PUBKEY}")"
  oc patch cm showroom-userdata -n "${ns}" --type=merge \
    -p "$(python3 -c "import json,sys; print(json.dumps({'data':{'user_data.yml': sys.argv[1]}}))" "${new_data}")"

  oc rollout restart deployment/showroom -n "${ns}"
done

echo
echo "Waiting for all ${LAB_USER_COUNT} showroom Deployments to come back up..."
for i in $(seq 1 "${LAB_USER_COUNT}"); do
  ns="showroom-user${i}"
  until [ "$(oc get deploy showroom -n "${ns}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" = "1" ] \
    && [ "$(oc get pods -n "${ns}" --no-headers 2>/dev/null | wc -l)" = "1" ]; do
    sleep 3
  done
  echo "${ns}: ready"
done

echo "Done."
