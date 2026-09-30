# Error Handling Standards

Evidenced from `app/services/*.py` (70+ custom exception classes) and
their call sites in `app/api/dashboard_routes.py`, `app/api/relay_routes.py`,
and `app/main.py`.

## 1. Every service module defines its own named exceptions

There is no shared `app/exceptions.py` and no generic `ValidationError` /
`AppError` base class used across the codebase. Instead, each
`app/services/<module>.py` that has real invalid-input or
invalid-transition cases defines its own `class <Condition>Error
(Exception): pass` at module scope, named for the *specific* condition it
signals, e.g.:

```python
# app/services/product_admin.py
class InvalidProductDraftError(Exception): ...
class SlugAlreadyExistsError(Exception): ...
class StaleRevisionError(Exception): ...

# app/services/release_review.py
class ReviewNotEligibleError(Exception): ...
class SelfReviewNotAllowedError(Exception): ...
class StaleReviewTargetError(Exception): ...
class InvalidReviewDecisionError(Exception): ...

# app/services/staff_access.py
class InvalidStaffGrantError(Exception): ...
class UnknownUserIdentityError(InvalidStaffGrantError): ...
class AlreadyAMemberError(InvalidStaffGrantError): ...
class MembershipNotFoundError(Exception): ...
class CannotRevokeOwnerError(Exception): ...
class CannotRevokeSelfError(Exception): ...
```

Rules to follow when adding a new one:

- **Name it after the condition, not the layer.** `StaleRevisionError`,
  not `ConflictError`; `SlugAlreadyExistsError`, not `DuplicateError`.
  A caller catching it should not need to open the service module to know
  what went wrong.
- **Subclass a narrower base only when there's a real "catch the general
  case, or one of these specific ones" need.** `staff_access.py`'s
  `UnknownUserIdentityError`/`AlreadyAMemberError` both subclass
  `InvalidStaffGrantError` so a caller that only wants "was this grant
  attempt invalid at all" can catch the parent, while a caller that needs
  to render a specific message can catch the child. `price_version.py`'s
  `SkuAlreadyExistsError(InvalidPriceVersionError)` is the same pattern.
  Do not subclass just for the sake of a hierarchy -- most exceptions here
  are plain `Exception` subclasses with no parent beyond that.
- **Suffix is always `Error`**, never `Exception` (`InvalidTokenError`,
  not `TokenException`) -- true of every custom exception in `app/services/`.
- **Two different modules independently defining an `InvalidTokenError`**
  (`app/services/auth.py` and `app/services/local_auth.py`) is accepted
  here precisely because these are two genuinely separate authentication
  paths (JWT bearer tokens vs. local password sessions) -- do not merge
  them into one shared class just because the names collide; import the
  one that matches the code you're actually calling.
- **Security-sensitive parsing/verification exceptions come in matched
  triples** naming the exact failure mode, not one generic
  `AuthError`: `relay_auth.py`'s `InvalidRelaySignatureHeaderError` /
  `RelaySignatureMismatchError` / `StaleRelayTimestampError`, and
  `stripe_webhook.py`'s `InvalidSignatureHeaderError` /
  `SignatureMismatchError` / `StaleTimestampError`. Follow this shape
  (malformed input / mismatch / staleness as three distinct classes) for
  any new signed-webhook or signed-request verifier.

## 2. Two different call-site conventions, by route type

This is a hybrid app: most routes render server-side HTML
(`app/api/dashboard_routes.py`), a few are JSON APIs
(`app/api/relay_routes.py`, webhook handlers in `app/main.py`). The error
handling convention differs by which kind of route it is -- match the one
already used by the route you're editing:

**Dashboard (HTML) routes** catch the service's specific exception(s),
roll back the session, and **re-render the same template** with the
existing data plus an inline error, rather than raising an `HTTPException`:

```python
try:
    revoke_staff_member(session, tenant_id=scope.tenant_id, user_id=user_id, acting_user_id=scope.user_id)
except (MembershipNotFoundError, CannotRevokeOwnerError, CannotRevokeSelfError) as exc:
    session.rollback()
    memberships = list_staff_memberships(session, tenant_id=scope.tenant_id)
    ...
    return templates.TemplateResponse(request, "ad16_staff.html", {..., "error": str(exc)})
```

The `except` clause always lists the **specific** exception classes the
call can actually raise (as a tuple when there's more than one), never a
bare `except Exception`. A caller only ever includes classes relevant to
what it's about to render/redirect differently for.

**JSON/API routes** (webhook receivers, the relay ingress) translate a
caught exception into an explicit `HTTPException` with a status code that
matches the failure kind, e.g. `app/main.py`:

```python
except (InvalidSignatureHeaderError, SignatureMismatchError, StaleTimestampError) as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
```

Always `raise ... from exc` (never swallow the original traceback) when
re-raising as an `HTTPException`.

## 3. Fail-closed on security-relevant verification

`app/services/auth.py`'s `verify_token` (the one real request-verification
call site, used by `app/api/dependencies.py::get_current_scope`) checks
the JWT revocation denylist after decoding and **fails closed**: "any
exception raised while checking revocation is treated as rejection, never
as 'not revoked'" (see the revocable-JWT-sessions commit). Any new
security check follows the same rule -- an exception during a permission/
revocation/signature check must never be interpreted as "allowed."

## 4. Real-database constraints are the actual guard, not just the exception

Some invariants are enforced by the database itself (Postgres RLS,
`FORCE ROW LEVEL SECURITY`, the `forbid_ledger_mutation` append-only
trigger -- see `docs/standards/CODING.md`) rather than solely by raising a
Python exception in the service layer. Where both exist (a service-layer
check *and* a DB-level constraint), treat the service-layer exception as
the primary UX path and the DB constraint as the structural backstop that
holds even if the service-layer check is ever bypassed or has a bug --
never remove the DB-level guard because "the service layer already checks
this."
