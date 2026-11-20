# Access control

Satisfies PRD §3 (roles and permissions) and §9.6 (authentication policy). Settings →
Access control.

## Roles

Two roles are supplied: Administrator, which holds every permission, and Sales Person. Both
may be renamed; neither may be removed. Further roles are created empty or copied from an
existing role, and their permissions are granted or withdrawn one at a time or a module at a
time. A role is removed only when nobody holds it. Every change records its reason.

## A person's exceptions

A permission may be granted to, or withheld from, one user beyond what their roles give, with
a reason, the person who set it and when. The effective set is the roles' permissions, plus
grants, less withholdings. Two rules:

- Nobody amends their own access; another administrator does.
- An administrator takes no exceptions. To narrow what someone may do, give them a different
  role.

## Sessions

Each person's sessions in force are listed with device and last activity, and can be ended
everywhere at once with a reason.

## Sign-in policy

| Setting | Bounds | Default |
| --- | --- | --- |
| Minimum password length | 8–64 | 10 |
| Upper case, lower case, digit, symbol | each on or off | on, on, on, off |
| Earlier passwords refused | 0–24 | 5 |
| Lock after failed attempts | 3–20 | 5 |
| Lock for | 1 minute–24 hours | 15 minutes |
| Sign out when idle | 5 minutes–8 hours | 30 minutes |
| Sign out after | 1–24 hours | 12 hours |

The installation's defaults apply until the business sets its own. A stricter password rule
applies at the next change of password; lockout and session limits apply at once.

## Permissions

| Action | Permission |
| --- | --- |
| View roles, people's access, sessions and the policy | `user.view` |
| Create, rename and remove roles; set permissions, exceptions and the policy | `role.manage` |
| End a person's sessions | `user.manage` |

Posting a business event to the ledger is authorised by the permission of the event itself:
a counter sale under `pos.operate`, a receipt of goods under its own. `journal.post` guards
only the entries a person writes by hand.
