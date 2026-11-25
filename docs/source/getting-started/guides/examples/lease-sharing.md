# Lease Sharing

A {term}`lease` is normally exclusive: the {term}`client` that acquires it is
the sole owner and no one else can reach the leased {term}`exporter`. Lease
sharing lets the owner grant **additional clients** read/observe/connect access
to the same lease while keeping ownership - useful for pairing on a debugging
session, letting a teammate watch a serial console, or handing a CI-held lease
to a human for inspection.

This guide covers {term}`distributed mode`; sharing is coordinated by the
{term}`controller` and therefore does not apply to {term}`local mode` or {term}`direct mode`
connections.

## Owners vs. shared clients

Sharing splits capabilities into two roles:

| Capability | Owner | Shared client |
| --- | --- | --- |
| Connect / dial the exporter (`jmp shell`, {term}`driver` calls) | Yes | Yes |
| Observe serial consoles | Yes | Yes |
| List/inspect the lease (`jmp get leases`) | Yes | Yes |
| Add or remove shared clients | Yes | No |
| Change duration / times | Yes | No |
| Transfer the lease to another client | Yes | No |
| Release or delete the lease | Yes | No |

In short, shared clients get **access** but not **control**: everything that
mutates the lease (sharing, timing, transfer, release, delete) is restricted to
the owner. A shared client that attempts one of these receives a
`PermissionDenied` error.

## Sharing at creation time

Share a lease with one or more clients as you create it using `--share` (a
comma-separated list of client names):

```{code-block} console
$ jmp create lease -l board=rpi4 --duration 2h --share client-alice,client-bob
```

A lease can be shared with at most 10 clients; a name given more than once
counts once.

## Managing sharing on an existing lease

Use the `jmp share` command group to change sharing after the lease exists. All
three subcommands take the lease name as the first argument.

```{code-block} console
$ jmp share add my-lease client-alice client-bob
$ jmp share remove my-lease client-alice
$ jmp share list my-lease
```

`jmp update lease my-lease --share-add client-alice --share-remove client-bob`
makes the same changes; both options can be repeated.

`jmp share list` distinguishes the owner's **intent** from what the controller
actually **granted**:

```{code-block} console
$ jmp share list my-lease
Lease my-lease shared with:
  client-bob
  client-alice  (not active: denied by policy or client not found)
```

A name shows as `not active` when it is in the owner's requested share set but
was filtered out - either because no client with that name exists, or because an
[exporter access policy](../../../reference/crds/exporteraccesspolicy.md) does
not allow that client to reach this exporter. See
[Access policies](#access-policies-and-effective-sharing) below.

## Working with a lease shared with you

A shared client does not create a lease - the owner already did. `jmp get leases`
lists the leases shared with you together with your own (`-A`/`--all-clients`
also adds leases you cannot reach):

```{code-block} console
$ jmp get leases
```

Leases you can reach but do not own show `<owner> (shared)` in the `CLIENT`
column so you can tell them apart from your own.

To open a shell against a lease shared with you, pass its ID with `--lease`:

```{code-block} console
$ jmp shell --lease my-lease
```

If you can reach exactly one active lease, `jmp shell` without `--lease`,
`--selector` or `--name` selects it automatically. When you can reach more than
one, it lists them (marking leases shared with you `shared by <owner>`) and asks
you to pick one if it runs in a terminal; otherwise it exits and asks for
`--lease`.

## Access policies and effective sharing

Sharing is always subject to the exporter's
[access policies](../../../reference/crds/exporteraccesspolicy.md). Adding a
client to a lease expresses *intent*; the controller then evaluates the policies
and computes the **effective** share set - the clients that are actually
granted access. A name that matches no client is rejected when you share, and so
is a client the policy denies if the lease already has an exporter. A client the
policy denies later (after a policy change, or once an exporter is assigned to a
lease shared at creation), or a client that has since been deleted, is silently
excluded from the effective set and never gains access.

This is why `jmp share list` reports two states: the requested list and whether
each entry is currently active.

## Revoking access

`jmp share remove` (and policy changes that newly deny a client) provide a
**soft** guarantee, not an instantaneous kill:

- The controller immediately stops granting the removed client **new**
  connections.
- A shared client's **live** `jmp shell` {term}`session` polls its own access and exits
  on its own within a short interval (currently 15 seconds), so there is a
  bounded window during which an already-open session can still be used. Its
  shell is sent `SIGHUP`, as when the lease ends.
- Removal does **not** forcibly tear down a {term}`router` stream the exporter has
  already established.

Transferring the lease (`jmp update lease my-lease --to-client client-carol`)
clears its share list, so the previous owner's and every shared client's
`jmp shell` session ends the same way.

If you need to cut off everyone immediately - including the owner and all shared
clients - end the lease itself:

```{code-block} console
$ jmp delete lease my-lease
```

## Serial consoles for shared sessions

Serial consoles are a common reason to share a lease: one person drives the
console while others watch. The PySerial driver supports a read-only **observe**
mode and an exclusive write token so multiple shared clients can attach to the
same console safely. See the "Serial console sharing and observe mode" section of
the {doc}`pyserial driver reference </reference/package-apis/drivers/pyserial>`
for `j serial console --observe`, `release-console`, and `console-status`.
