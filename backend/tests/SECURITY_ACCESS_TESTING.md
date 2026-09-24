### 3.1.6 Security and Access Control Testing

Security and Access Control Testing focuses on two key areas of security:

- Application-level security, including access to the data or business functions
- System-level security, including logging into or remotely accessing the system

Application-level security ensures that actors are restricted to specific functions or use cases, or are limited in the data that is available to them. In TrendCast there is a single user type (a signed-in creator), so access control is about **ownership**: every user may create and read their own predictions, notifications, profile and channel snapshot, and no user may read or change another user's. Anonymous callers may only sign up, log in and check health.

System-level security ensures that only those users granted access to the system can reach the application, and only through the appropriate gateways: the API with a valid token, and the database only through the backend's owner connection. It also ensures that a caller who reaches the application layer cannot reach the database layer directly.

**Technique Objective:**

Exercise the target-of-test under the following conditions to observe and log target behaviour:

- **Application-level security:** an actor can access only those functions and data for which they hold permission. A user cannot type an id or URL and read or change data that belongs to someone else.
- **System-level security:** only actors with valid credentials reach the API, tokens cannot be forged or replayed beyond their intended life, secrets are required at startup, and a non-owner database role (for example Supabase's `anon` / `authenticated` keys) cannot read or write any table.
- **Input attacks:** hostile input (SQL injection, script payloads, hostile uploads, oversized bodies, internal URLs) is contained and does not leak internals.

**Technique:**

- Application-level security:
  - Identify every route and classify it as public, or protected (needs a valid JWT). A route inventory test walks the FastAPI route table, so a new route added without authentication fails until it is classified.
  - Create two users (Ann and Bob) and verify each ownership rule: Bob cannot read, delete or mark-read Ann's predictions or notifications, list endpoints never include Ann's rows, and "not yours" is indistinguishable from "does not exist".
  - Send forbidden requests (anonymous, forged token, expired token, malformed `Authorization` header, token of a deleted user) and verify a 401 or 404 is returned with no data.
  - Attempt mass assignment (`id`, `email`, `password_hash`, `channel_data` in a profile update) and verify protected columns are unchanged.
- System-level security:
  - Verify login does not reveal whether an email exists, and observe behaviour under repeated failed logins.
  - Verify token integrity: tampered subject, wrong secret, `alg: none`, wrong algorithm, missing `exp`, and a token issued before a password change.
  - Start the backend with each required secret empty and verify startup fails with a clear error.
  - Create a login-less database role holding every table privilege and verify row-level security alone hides all rows and rejects writes.
  - Verify `.env` files are not tracked by git and `.env.example` holds placeholders only.
- Input attacks:
  - Send SQL injection payloads through prediction text fields, login, profile, signup email and the public path parameters, and verify the data is stored or rejected as plain text and no table is altered.
  - Upload files with forged content types, executable extensions, path-traversal filenames and a decompression-bomb image.
  - Force server errors and inspect the response body for stack traces, SQL or secrets.
  - Post internal and non-HTTP thumbnail URLs to `/forecast` (server-side request forgery).
- Browser-facing:
  - Send CORS preflights from look-alike, `null` and wrong-scheme origins and verify none are allowed.
  - Check responses for basic security headers.

**Oracles:**

The tests are automated with pytest and run the real FastAPI routers on top of a disposable Postgres container. The YouTube lookup and the forecast model are faked, so results depend only on the application's own security logic. The oracle for each case is the HTTP status code and body, the state of the database afterwards (queried directly), and the files on disk.

Tests that record a known weakness are marked `xfail(strict=True)`. The assertion states the secure behaviour; while the gap exists the test is reported as "expected failure". When the gap is fixed the test flips to unexpectedly passing, strict mode fails the run, and the marker must be removed. This keeps the suite green today while making every finding visible and self-verifying.

The suite refuses to run against the live database: the test harness points `SUPABASE_DB_URL` at the throwaway container and asserts that it took effect.

**Required Tools:**

- pytest, FastAPI `TestClient` (httpx), PyJWT, Pillow, psycopg2
- Docker (disposable `pgvector/pgvector:pg16` container)
- git (for the tracked-secrets check)
- Test file: `backend/tests/test_security_access.py`; run with `cd backend && python -m pytest tests/test_security_access.py`
- Not used in this run (recommended follow-up): OWASP ZAP for headers and active scanning, sqlmap against a local instance, `pip-audit`, `npm audit`, `bandit -r backend`

**Success Criteria:**

- A caller can only reach the functions and data they own; every other request receives 401 or 404 and changes nothing.
- Every route in the application is either protected or on the explicit public list.
- Tokens cannot be forged, tampered with or replayed after their lifetime, and the backend will not start without its secrets.
- A non-owner database role sees zero rows and cannot write.
- Hostile input is stored or rejected as plain text and never alters the schema, escapes the uploads directory, or exposes internals.
- Any gap found is recorded with an ID, severity and a test that will turn green when it is fixed.

**Special Considerations:**

- TrendCast has no role hierarchy, so the "modify user type and re-run" step of the technique does not apply. Horizontal (user-to-user) access control is the risk that matters.
- The backend connects to the database as its owner, which bypasses row-level security. RLS therefore defends against the Supabase REST API and other roles, not against a bug in the backend's own queries; ownership is enforced in the SQL (`WHERE user_id = ...`) and verified by the tests above.
- All queries are parameterised psycopg2 statements (no ORM), so SQL injection testing stays in scope. The single dynamic SQL fragment, the `SET` clause in `PATCH /auth/me`, is built from Pydantic field names only.
- Several read-only pipeline endpoints (`/channels`, `/channels/{id}/videos`, `/videos/{id}/timeseries`, `/forecast/health`) are public today. The tests document this; whether it is intended is an open decision (S-A7).
- The tests were run only against a local disposable database, never the live Supabase instance, because they create and delete users and include brute-force and oversized-input cases.

---

#### Results

**Run:** `python -m pytest tests/test_security_access.py`, 88 test cases, 225 s, against a disposable Postgres 16 container (Windows 11, Python with bcrypt 5.0.0, PyJWT 2.10.1, FastAPI 0.116.0).

**Outcome: 64 passed, 24 expected failures (known gaps), 0 unexpected failures.**

| Area | Cases | Passed | Known gaps (xfail) |
|---|---|---|---|
| S-A Application-level access control | 22 | 20 | 2 |
| S-B System-level security | 18 | 11 | 7 |
| S-C Input attacks | 40 | 26 | 14 |
| S-D Browser-facing (CORS, headers) | 8 | 7 | 1 |
| **Total** | **88** | **64** | **24** |

##### Controls verified

| ID | Control | Result |
|---|---|---|
| S-A1 | Every route is protected or on the explicit public list; anonymous, forged, expired, subject-less, `alg: none` and malformed-header requests get 401 | Pass |
| S-A2 | Bob cannot read or delete Ann's predictions or mark her notifications read; lists never include another user's rows; `read-all` only touches the caller | Pass |
| S-A3 | "Not yours" and "does not exist" return identical status and body | Pass |
| S-A4 | Profile update cannot change `id`, `email`, `password_hash`, `channel_data`, `channel_url`; negative numbers get 422; wrong current password leaves the hash unchanged | Pass |
| S-A5 | Token for a deleted user is rejected | Pass |
| S-A6 | Another user's delete keeps the owner's files | Pass |
| S-A8 | No response contains the password hash | Pass |
| S-B1 | Login gives the same response for wrong password and unknown email | Pass |
| S-B5 | Tampered subject and HS512-signed tokens are rejected; token lifetime is at most 7 days | Pass |
| S-B6 | Backend fails to start when `JWT_SECRET_KEY`, `SUPABASE_DB_URL` or `YOUTUBE_API_KEY` is empty | Pass (3 cases) |
| S-B7 | A non-owner role with every privilege sees zero rows in every table and cannot insert, update or delete | Pass |
| S-B8 | No `.env` file is tracked by git; `.env.example` has no real-looking secret | Pass |
| S-C1 | SQL injection payloads (5 payloads x prediction fields, login/profile, signup email, path parameters) do not bypass login or alter any table | Pass (20 cases) |
| S-C3 | A 15000x15000 decompression-bomb PNG is rejected with 400 | Pass |
| S-C2 | Thumbnail content type is allow-listed (HTML, SVG, octet-stream, GIF rejected) | Pass |
| S-C4 | Path-traversal, NUL and 5000-character filenames do not escape the uploads directory or crash | Pass |
| S-C6 | Script/HTML payloads come back as `application/json` data, not markup | Pass (API level) |
| S-C7 | A forced server error returns no stack trace, SQL or secret | Pass |
| S-D1 | CORS rejects `null`, look-alike, wrong-scheme, wrong-port and wrong-case origins and never answers with `*` | Pass (7 cases) |

##### Findings (known gaps)

| ID | Severity | Finding | Evidence | Suggested fix |
|---|---|---|---|---|
| S-C5b | High | `/forecast` fetches any `thumbnail_url` server-side (SSRF): metadata service, localhost, private ranges, `file://` and `gopher://` are not blocked | 7 URL cases reach the model call | Require login; allow only public `http(s)` hosts and block private/loopback/link-local addresses after DNS resolution |
| S-C5a | High | `/forecast` needs no login, so anyone can consume model compute and YouTube quota | Anonymous POST returns 200 | Add `Depends(get_current_user)` or a rate limit |
| S-C2b | High | Uploaded files keep the client's extension and are served from the API origin, so an uploaded `.html`/`.svg`/`.js` file is stored XSS | 4 extensions kept | Store with a fixed extension per validated type; serve with `Content-Disposition: attachment` and `nosniff`, or from a separate origin |
| S-C2 | Medium | A draft's "thumbnail" is never decoded, so any bytes with an image content type are stored | HTML bytes accepted as `image/png` | Decode with Pillow on save for drafts too |
| S-B2 | Medium | No rate limiting or lockout on `/auth/login` | 15 wrong guesses all answered 401 | Per-IP and per-account throttling |
| S-B5c | Medium | A token issued before a password change stays valid for its full 7 days; there is no revocation | Old token still returns 200 | Store a token version or `password_changed_at` and check it in `get_current_user` |
| S-B5b | Medium | Tokens without an `exp` claim are accepted and never expire | Token with no `exp` returns 200 | `jwt.decode(..., options={"require": ["exp"]})` |
| S-B4a | Medium | A password over 72 bytes makes bcrypt 5 raise, returning a 500 on signup | 100-character password returns 500 | Reject or pre-hash passwords above 72 bytes; return 422 |
| S-B4b | Medium | Passwords have no maximum length; a 1 MB password returns 500 | 1,000,000 characters returns 500 | `max_length` on the password fields |
| S-A6 | Medium | Deleting a prediction removes the row but leaves its thumbnail and dataset in `/uploads`, still served by URL | File still on disk after `DELETE` | Call `delete_upload` for both paths on delete |
| S-B3 | Low | Signup answers "An account with that email already exists", revealing registered emails | Message present | Accept the trade-off, or respond generically and email the owner |
| S-B3b | Low | Emails are case-sensitive, so `Ann@x.com` and `ann@x.com` are separate accounts | Both signups return 200 | Lower-case on signup and login, with a unique index on `lower(email)` |
| S-A4b | Low | `subscribers` / `monthly_views` have no upper bound; a value above BIGINT returns a 500 | `10**30` returns 500 | `le=` bound in `UpdateProfileRequest` |
| S-C7 | Low | An invalid-image 400 echoes the raw Pillow exception text | `BytesIO` object address in the body | Return a fixed message and log the detail |
| S-D2 | Low | No `X-Content-Type-Options` or other security headers | Header absent | Add a small middleware (`nosniff`, `X-Frame-Options`, HSTS at the proxy) |

##### Open decisions and residual risks (not test failures)

- **S-A7:** the read-only pipeline endpoints are public today. Decide whether that is intended.
- The JWT is stored in `localStorage` in the frontend, so any XSS steals it. An `HttpOnly` cookie would remove that exposure.
- The backend's database role and connection privileges on the live Supabase project were not inspected. Confirm it is not a superuser and that only the connection string is secret.
- S-C6 was checked at API level only. Confirm in the browser that titles, names and notifications render escaped.

##### Not covered in this run

- OWASP ZAP active scan, sqlmap, `pip-audit`, `npm audit` and `bandit` were not run.
- The live deployment was not tested; a read-only smoke check (health, 401 on protected routes, CORS, headers, upload traversal) is the safe way to add that.
