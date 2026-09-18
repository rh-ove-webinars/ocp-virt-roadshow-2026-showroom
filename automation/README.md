# AAP setup automation (instructor-run, not part of the lab guide)

Installs Ansible Automation Platform's Automation Controller on the roadshow cluster and
provisions one Organization + one local User per attendee (`user1`..`user20`), plus the
Project/Credentials/Inventory/Job Template each org needs for Module 8 of the lab guide
(`content/modules/ROOT/pages/module-08-aap.adoc`).

This is infrastructure setup run **once, before the event**, by whoever has cluster-admin on
the shared lab cluster. Attendees never touch these playbooks.

## Prerequisites

- `oc login` as a cluster-admin, against the lab cluster, before running anything here.
- `ansible-core` plus the `kubernetes.core` collection on the machine you run this from
  (`ansible-galaxy collection install kubernetes.core`).
- The AAP operator's catalog source (`redhat-operators`) available on the cluster.
- You know the **shared OpenShift password** all 20 attendees will use (`{password}` in the
  lab guide) -- pass it as `lab_password`.

## Run it

```console
# 1. Install AAP (Automation Controller only -- no Hub/EDA, to keep the footprint small
#    on a cluster that's also running ODF and 20 attendees' VMs). Takes ~10-15 minutes.
ansible-playbook install-aap.yml

# 2. Configure orgs/users/content. Safe to re-run any time (e.g. to refresh attendees'
#    OpenShift OAuth tokens shortly before the event -- see note below).
ansible-playbook configure-controller.yml -e lab_password='<the shared OCP lab password>'
```

Or run both in sequence with `ansible-playbook site.yml -e lab_password=...` (step 2 still
needs `lab_password` passed explicitly; nothing here should ever hardcode real lab
credentials into a committed file).

`install-aap.yml` writes the Controller route and generated admin password to
`.generated/controller-connection.yml` (gitignored) so `configure-controller.yml` picks them
up automatically. `configure-controller.yml` also generates a lab-wide SSH keypair into
`.generated/` the first time it runs.

## What gets created, per attendee (`userN`)

- Organization `userN`, local User `userN` (password = `lab_password`) as its Admin.
- Machine credential "VM SSH Key" -- the same lab-wide keypair for every org.
- Credential "OpenShift Access" -- a Bearer Token credential built from an OAuth token for
  that attendee's *own* OpenShift identity (see "Design notes" below).
- Project "Roadshow Automation" -- SCM sync of this repo, which provides both the pre-built
  demo playbook (`vm-content/playbooks/patch-vm.yml`) and the dynamic inventory plugin config
  (`vm-content/inventory/vms.kubernetes.yml`).
- Inventory "vmexamples-userN" with an Inventory Source using that plugin config (sync is
  **not** triggered automatically -- attendees click "Sync" themselves in Module 8, which is
  the "discover your VM" moment).
- Job Template "Patch fedora01 - userN".

## Design notes / things to double check on the real cluster

- **No ServiceAccounts/RBAC to manage.** Attendees' namespaces (`vmexamples-userN`) don't
  exist until they run Module 1 of the lab, so this can't pre-create namespace-scoped RBAC.
  Instead, each org's Kubernetes credential is an OAuth token minted by logging in *as that
  attendee* (`oc login -u userN -p <lab_password>`) -- which works even before their
  namespace exists, since login/identity is independent of it. This also means each
  attendee's dynamic inventory can only ever discover what that attendee's own account can
  see -- free per-user isolation, nothing to lock down manually.
- **Token lifetime.** These OAuth tokens expire per the cluster's
  `accessTokenMaxAgeSeconds` OAuth setting (often 24h by default on RHDP clusters). If the
  cluster is provisioned well ahead of the event, re-run `configure-controller.yml` shortly
  before it starts to refresh every attendee's token.
- **SSH key injection.** Module 8 has attendees paste the lab's public key
  (`.generated/aap_lab_id_rsa.pub` after the first `configure-controller.yml` run) into their
  `fedora01` VM's *SSH* configuration tab, using OpenShift Virtualization's public-key
  injection feature (already referenced in Module 1 of the guide). **Verify this feature is
  actually enabled on the cluster's OpenShift Virtualization install before the event** -- if
  it isn't, that one step in Module 8 needs to be reworked (e.g. cloud-init on a freshly
  created VM instead of reusing `fedora01`).
- **Antora attributes.** Module 8 references two new attributes, `{aap_console_url}` and
  `{aap_ssh_pubkey}`, following the same convention as the existing `{user}`/`{password}`/
  `{openshift_console_url}` (referenced in content but supplied externally by whatever
  RHDP/Showroom tooling injects those today -- not defined anywhere in this repo). Wire these
  two in the same place once the Controller route and generated public key are known; both
  are printed at the end of the `ansible-playbook` runs above.
