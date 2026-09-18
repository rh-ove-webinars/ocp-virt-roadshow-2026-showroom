# AAP setup automation (instructor-run, not part of the lab guide)

Installs a unified Ansible Automation Platform instance (gateway + Controller + EDA; Hub and
Lightspeed disabled) on the roadshow cluster and provisions one Organization + one local User
per attendee (`user1`..`user20`), plus the two Credentials each org needs ("VM SSH Key" and
"OpenShift Access") for Module 8 of the lab guide
(`content/modules/ROOT/pages/module-08-aap.adoc`). Attendees build the Project, Inventory and
Job Template themselves in that module -- this automation deliberately stops at credentials.

This is infrastructure setup run **once, before the event**, by whoever has cluster-admin on
the shared lab cluster. Attendees never touch these playbooks. It also patches the
`showroom-userN` Deployments RHDP provisions (repoint them at this fork, fix a rollout
deadlock, wire in the two new Antora attributes Module 8 needs) -- see step 3 below.

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
  something any of the playbooks/scripts here automate.

## Run it

```console
# 1. Install AAP (gateway + Controller + EDA; Hub/Lightspeed disabled to keep the footprint
#    small on a cluster that's also running ODF and 20 attendees' VMs). Takes ~10-15 minutes.
ansible-playbook install-aap.yml

# --- attach a subscription in the AAP UI here (see Prerequisites) before continuing ---

# 2. Configure orgs/users/credentials. Safe to re-run any time (e.g. to refresh attendees'
#    OpenShift OAuth tokens shortly before the event -- see note below).
ansible-playbook configure-controller.yml -e lab_password='<the shared OCP lab password>'

# 3. Patch the 20 showroom-userN Deployments RHDP provisions: repoint them at this fork,
#    fix a rollout deadlock, and wire in {aap_console_url}/{aap_ssh_pubkey}. Must run AFTER
#    steps 1 and 2 (it reads the AAP route and the SSH key configure-controller.yml
#    generated). See the script's own header comment for exactly what it does and why.
./fixup-showroom-deployments.sh
```

Or run steps 1-2 in sequence with `ansible-playbook site.yml -e lab_password=...` (still
needs `lab_password` passed explicitly; nothing here should ever hardcode real lab
credentials into a committed file). Step 3 is a separate shell script, not part of `site.yml`,
since it isn't Ansible-Automation-Platform-specific -- it just patches whatever RHDP's own
provisioning created in the `showroom-userN` namespaces.

`install-aap.yml` writes the gateway route and generated admin password to
`.generated/controller-connection.yml` (gitignored) so `configure-controller.yml` picks them
up automatically. `configure-controller.yml` also generates a lab-wide SSH keypair into
`.generated/` the first time it runs, which `fixup-showroom-deployments.sh` then reads.

**Changing `aap_operator_channel` on an existing install:** patching the Subscription's
`spec.channel` in place does **not** reliably make OLM jump to the new channel's CSV (AAP's
channels aren't necessarily one continuous replaces-chain) -- it can just sit at
`AtLatestKnown` on the old CSV indefinitely. Delete the Subscription and the installed CSV,
then let `install-aap.yml` recreate the Subscription fresh against the new channel instead
(and expect to also clean up the old instance's PVCs/generated secrets first if you want a
truly clean database rather than carrying over old data into the new version).

**Re-running `configure-controller.yml` after Module 8 content has been in use:** it's fully
idempotent for Organizations/Users/Credentials, but attendees' own Projects/Inventories/Job
Templates (which they build themselves per the module) are untouched either way -- this
playbook never looks at or touches those.

## What gets created, per attendee (`userN`)

- Gateway Organization `userN`, gateway User `userN` (password = `lab_password`), granted the
  gateway's "Organization Admin" role on that org. AAP federates this down to Controller
  automatically (near-instantly, observed) -- Controller's own `/api/controller/v2/...` API
  is what the rest of the setup uses, since credentials are still Controller-specific objects,
  just reached through the gateway's proxied path.
- Machine credential "VM SSH Key" -- the same lab-wide keypair for every org. Its matching
  public key is what attendees paste into their new VM's cloud-init in Module 8.
- Credential "OpenShift Access" -- a Bearer Token credential built from an OAuth token for
  that attendee's *own* OpenShift identity (see "Design notes" below).

Nothing else -- no Project, Inventory, or Job Template. Attendees create those themselves in
Module 8, referencing these two credentials by name.

## Design notes / things to double check on the real cluster

- **Gateway vs. Controller API.** AAP 2.5+ centralizes auth/RBAC (Organizations, Users,
  Teams, role assignments) in the gateway (`/api/gateway/v1/...`). Controller-specific
  objects (Credentials, Projects, Inventories, Job Templates) still live in Controller, but
  are only reachable in 2.7 through the gateway's proxied path
  (`{{ aap_host }}/api/controller/v2/...`) using the *same* gateway admin credentials -- there
  is no separate standalone Controller route/login in the unified platform deployment.
  `tasks/configure_user.yml` creates the org and user via the gateway API, waits for it to
  federate to Controller's own org list (by name), then uses Controller's *own* numeric id
  for that org for the credentials -- it is not guaranteed to match the gateway's numeric id
  for the same org.
- **A `uri` module quirk that cost real debugging time:** a Jinja-templated integer nested
  inside a `body:` dict on an `ansible.builtin.uri` task gets re-stringified before being
  JSON-encoded (confirmed with `-vvv`; `| int` does not survive it). Most Controller API
  fields tolerate a numeric string (DRF's `PrimaryKeyRelatedField` coerces it), but at least
  one endpoint (the job-template-credential association endpoint, back when this was still
  pre-creating job templates) rejected it outright with `"id" field must be an integer.`. If
  you add any new `uri` task with an `id`-like field built from a Jinja expression rather than
  a literal, pre-serialize the body yourself with `| to_json` and `body_format: raw` rather
  than trusting `body_format: json` to type it correctly.
- **No ServiceAccounts/RBAC to manage.** Attendees' namespaces (`vmexamples-userN`) don't
  exist until they run Module 1 of the lab, so this can't pre-create namespace-scoped RBAC.
  Instead, each org's Kubernetes credential is an OAuth token minted by logging in *as that
  attendee* (`oc login -u userN -p <lab_password>`) -- which works even before their
  namespace exists, since login/identity is independent of it. This also means each
  attendee's dynamic inventory (which they build themselves in Module 8) can only ever
  discover what that attendee's own account can see -- free per-user isolation, nothing to
  lock down manually.
- **Token lifetime.** These OAuth tokens expire per the cluster's
  `accessTokenMaxAgeSeconds` OAuth setting (often 24h by default on RHDP clusters). If the
  cluster is provisioned well ahead of the event, re-run `configure-controller.yml` shortly
  before it starts to refresh every attendee's token.
- **SSH key injection is done via cloud-init at VM-create time**, not by editing an
  already-running VM's SSH configuration -- an earlier draft of Module 8 relied on OpenShift
  Virtualization's SSH-public-key-injection feature for an already-running VM, but that
  depends on a feature gate this session couldn't confirm was enabled, so the module was
  redesigned to have attendees create a fresh VM with the key baked into cloud-init instead
  (the same pattern Module 5 already uses), which has no such dependency.
- **`showroom-userN` Deployments are not defined in this repo.** They're created by RHDP's
  own provisioning (Helm chart / AgnosticD / whatever the catalog item uses), which this repo
  has no source access to. `fixup-showroom-deployments.sh` (step 3 above) patches three
  things directly on the live Deployments/ConfigMaps after every fresh cluster provision,
  none of which survive a cluster rebuild on their own -- see that script's header comment
  for the full detail on each (repointing `GIT_REPO_URL`/`GIT_REPO_REF` at this fork,
  switching to `strategy.type: Recreate` to avoid an RWO-volume rollout deadlock, and wiring
  `{aap_console_url}`/`{aap_ssh_pubkey}` into each `showroom-userdata` ConfigMap).
