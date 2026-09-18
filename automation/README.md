# AAP setup automation (instructor-run, not part of the lab guide)

Installs a unified Ansible Automation Platform instance (gateway + Controller + EDA; Hub and
Lightspeed disabled) on the roadshow cluster and provisions one Organization + one local User
per attendee (`user1`..`user20`), plus the Project/Credentials/Inventory/Job Template each org
needs for Module 8 of the lab guide (`content/modules/ROOT/pages/module-08-aap.adoc`).

This is infrastructure setup run **once, before the event**, by whoever has cluster-admin on
the shared lab cluster. Attendees never touch these playbooks.

## Prerequisites

- `oc login` as a cluster-admin, against the lab cluster, before running anything here.
- `ansible-core` plus the `kubernetes.core` collection on the machine you run this from
  (`ansible-galaxy collection install kubernetes.core`).
- The AAP operator's catalog source (`redhat-operators`) available on the cluster.
- You know the **shared OpenShift password** all 20 attendees will use (`{password}` in the
  lab guide) -- pass it as `lab_password`.
- A Red Hat subscription/manifest to attach to the new AAP instance. Without one, the gateway
  UI blocks every login behind a "enter your Red Hat subscription details" screen -- it does
  **not** block the API, so `configure-controller.yml` still works either way, but attendees
  will not be able to log into the AAP UI in Module 8 until this is attached. Log into the
  gateway as `admin` (password from `install-aap.yml`'s output / `aap-admin-password` secret)
  and attach a subscription (e.g. a Red Hat service account created for this) the first time
  after each fresh install -- this is a manual, one-time-per-install step done in the UI, not
  something either playbook here automates.

## Run it

```console
# 1. Install AAP (gateway + Controller + EDA; Hub/Lightspeed disabled to keep the footprint
#    small on a cluster that's also running ODF and 20 attendees' VMs). Takes ~10-15 minutes.
ansible-playbook install-aap.yml

# 2. Configure orgs/users/content. Safe to re-run any time (e.g. to refresh attendees'
#    OpenShift OAuth tokens shortly before the event -- see note below).
ansible-playbook configure-controller.yml -e lab_password='<the shared OCP lab password>'
```

Or run both in sequence with `ansible-playbook site.yml -e lab_password=...` (step 2 still
needs `lab_password` passed explicitly; nothing here should ever hardcode real lab
credentials into a committed file).

`install-aap.yml` writes the gateway route and generated admin password to
`.generated/controller-connection.yml` (gitignored) so `configure-controller.yml` picks them
up automatically. `configure-controller.yml` also generates a lab-wide SSH keypair into
`.generated/` the first time it runs.

**Changing `aap_operator_channel` on an existing install:** patching the Subscription's
`spec.channel` in place does **not** reliably make OLM jump to the new channel's CSV (AAP's
channels aren't necessarily one continuous replaces-chain) -- it can just sit at
`AtLatestKnown` on the old CSV indefinitely. Delete the Subscription and the installed CSV,
then let `install-aap.yml` recreate the Subscription fresh against the new channel instead
(and expect to also clean up the old instance's PVCs/generated secrets first if you want a
truly clean database rather than carrying over old data into the new version).

## What gets created, per attendee (`userN`)

- Gateway Organization `userN`, gateway User `userN` (password = `lab_password`), granted the
  gateway's "Organization Admin" role on that org. AAP federates this down to Controller
  automatically (near-instantly, observed) -- Controller's own `/api/controller/v2/...` API
  is what the rest of the setup uses, since credentials/projects/inventories/job templates are
  still Controller-specific objects, just reached through the gateway's proxied path.
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

- **Gateway vs. Controller API.** AAP 2.5+ centralizes auth/RBAC (Organizations, Users,
  Teams, role assignments) in the gateway (`/api/gateway/v1/...`). Controller-specific
  objects (Credentials, Projects, Inventories, Job Templates) still live in Controller, but
  are only reachable in 2.7 through the gateway's proxied path (`{{ aap_host }}/api/controller/v2/...`)
  using the *same* gateway admin credentials -- there is no separate standalone Controller
  route/login in the unified platform deployment. `tasks/configure_user.yml` creates the org
  and user via the gateway API, waits for it to federate to Controller's own org list (by
  name), then uses Controller's *own* numeric id for that org for everything else -- it is
  not guaranteed to match the gateway's numeric id for the same org.

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
- **Antora attributes.** Module 8 references two new attributes, `{aap_console_url}` (the
  gateway URL) and `{aap_ssh_pubkey}` (the generated public key), following the same
  convention as `{user}`/`{password}`/`{openshift_console_url}`. On this cluster those are
  supplied per attendee via the `showroom-userdata` ConfigMap's `user_data.yml` in each
  `showroom-userN` namespace (mounted into the `content` container that builds the Antora
  site) -- add `aap_console_url`/`aap_ssh_pubkey` keys there for all 20 namespaces and
  restart each `showroom` Deployment so the site rebuilds with them.
