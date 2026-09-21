# Staff management

## Model

```
accounts  (a person's sign-in: one per email)
   1 ─ n
users     (the MEMBERSHIP: one per shop the person belongs to)  ─ n ─ 1  roles ─ n ─ role_permissions
   n ─ 1
shops
```

`users` **is** the membership, deliberately. Thirty-odd tables record who did something as `(shop_id, created_by) → users(shop_id, id)`,
and that composite key is what makes a row of one shop unable to point at a person of another. A separate memberships table would
duplicate it and break those keys. So a membership has: `account_id`, `shop_id`, `role_id`, `status`, `invited_by`, `joined_at`,
`removed_at`, `last_active_at` (plus the name and email shown in that shop). `users.role` (OWNER/STAFF) remains as a coarse legacy label
kept in step with the role.

**Status:** `INVITED`, `ACTIVE`, `SUSPENDED`, `REMOVED`. Nothing is deleted: **REMOVED keeps the row**, so history (audit entries, the
"created by" of documents) still names the person. Inviting a removed person again brings back the *same* membership.

## Multi-shop

One account can have memberships in several shops. After sign-in the person chooses one (`/auth/select-shop`); the choice is stored in
the session and validated against the account's own memberships on the server. Switching shops in the app clears what was loaded for
the previous one. A membership that is suspended or removed is not offered and stops working at once.

## Inviting

`POST /api/v1/staff/invitations {email, role_id}` (permission `STAFF_INVITE`):

1. the role must be assignable by the inviter (see the escalation rule in `RBAC.md`);
2. a random token is generated (`token_urlsafe(32)`); only its **SHA-256 hash** is stored; the token is returned **once**, inside a link
   `<KIRANA_FRONTEND_URL>/accept-invitation#token=…` (in the URL *fragment*, which browsers never send to a server or log);
3. **no email is sent**: no provider is bundled and none is faked. The screen says so and the inviter hands the link over themselves;
4. a new invitation for the same email replaces the one still waiting; the token expires after `KIRANA_INVITATION_EXPIRY_HOURS` (72).

`POST /auth/invitations/preview {token}` shows the shop, role and email. `POST /auth/invitations/accept {token, password, full_name}`:
locks the invitation row, so it works **once**; unknown, used, revoked and expired tokens all get the same 404 "This invitation is no longer
valid."; a new person chooses a password (the password rules apply and a refusal leaves the invitation usable); a person who already has an
account proves it with their password. Guessing is rate limited. The accepted person is signed in to that shop.

## Screens and API

| | |
| --- | --- |
| Staff page `/staff` | name, email, role, status, joined, last active, permission count, view permissions; invite; change role; suspend; let back in; remove; waiting invitations and withdrawing them |
| Roles page `/staff/roles` | system and custom roles, permission grid by group, create/edit/retire/restore a custom role |
| API | `GET/PATCH /staff`, `/staff/{id}`, `POST /staff/{id}/suspend|reactivate|remove`, `GET/POST /staff/invitations`, `POST /staff/invitations/{id}/revoke`, `GET/POST/PATCH /roles`, `/roles/{id}/deactivate|reactivate`, `GET /roles/permissions` |

Suspending and removing ask for confirmation and say what will happen. There are no DELETE routes.

## Rules

Nobody edits their own access; you cannot manage someone with more access than you; you cannot give a permission you lack; a shop always keeps
an active owner; a custom role cannot hold owner-only permissions and cannot be retired while in use (members are not silently reassigned:
move them first). Suspending or removing a member **ends their sessions at once**.

## Audit

Written to the shop's audit log with the actor, the target (`entity_type`/`entity_id`), the time, the request id and safe metadata (never a
password, token or hash): `login`, `logout`, `select_shop`, `password_changed`, `invitation_created`, `invitation_revoked`,
`invitation_accepted`, `role_changed` (with permissions added/removed), `staff_suspended`, `staff_reactivated`, `membership_removed`,
`role_created`, `permission_changed`, `role_updated`, `role_deactivated`, `role_reactivated`, `export`. Failed sign-ins (no shop is known) are
platform security events for operators. Owners and accountants read the shop's log at `GET /audit-log` (`AUDIT_LOG_VIEW`).

## Not built

**Ownership transfer** (it needs owner re-authentication, an explicit confirmation and a preserved history; a second owner can be invited
instead, and the last owner cannot be removed), self-service password reset, email delivery of invitations, MFA, and per-branch access.
