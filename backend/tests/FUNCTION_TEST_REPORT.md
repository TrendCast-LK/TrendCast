# TrendCast – Function Test Report

| | |
|---|---|
| **System** | TrendCast – YouTube view forecasting web application (FastAPI backend, PostgreSQL on Supabase, React frontend) |
| **Document** | Function Testing – test report |
| **Test date** | 20 September 2026 |
| **Status** | Backend function testing complete. One low-severity defect is open (see section 5). Manual login/logout and user interface checks are not part of this report. |

---

## 1. Evaluation Mission and Test Motivation

TrendCast lets a content creator forecast the first seven days of views for a planned YouTube video. A user signs up with a link to their channel, uploads a title and thumbnail, and receives a forecast that is saved, summarised on a dashboard and trends page, and announced by a notification.

Function testing checks that each of these functions gives the right result for valid input, gives a clear error for invalid input, applies the business rules, and is available only to the users who are allowed to use it. In TrendCast the business logic lives in the FastAPI routers (the "controllers"), so function testing was done through the HTTP API, before and apart from the user interface, as the test plan recommends.

The objectives of this testing were:

- Verify that every use case works with valid data and returns the expected result.
- Verify that invalid data is rejected with the right error and that nothing is written when a request is rejected.
- Verify that each business rule is applied (draft versus completed predictions, size limits, confidence bounds, polling cadence and so on).
- Verify that every protected function is reachable only by a signed-in user, and that a user can only reach their own data.
- Verify that the public data endpoints, the forecast endpoint and the file-serving endpoint behave correctly, including for unknown ids and hostile paths.

## 2. Target Test Items

| Item | Description |
|---|---|
| Authentication and profile | `POST /auth/signup`, `POST /auth/login`, `GET` and `PATCH /auth/me`, `POST /auth/change-password`; JWT issuing and checking |
| Channel | `GET /channel/me`, `POST /channel/refresh`; channel URL parsing |
| Notifications | `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all` |
| Predictions | `POST /predictions` (draft and completed), `GET /predictions`, `GET` and `DELETE /predictions/{id}`; upload storage |
| Dashboard and trends | `GET /dashboard/summary`, `GET /trends/summary` |
| Public data and forecast | `GET /health`, `/channels`, `/channels/{id}/videos`, `/videos/{id}/timeseries`, `GET /forecast/health`, `POST /forecast` |
| File serving and CORS | `/uploads` static files; cross-origin rules for the frontend |
| Business-rule functions | Schedule parsing, ISO-8601 duration parsing (the forecast model's most important input), video polling interval by age |

Out of scope for this report: the user interface (section 7), forecast model accuracy, and load and performance behaviour.

## 3. Testing Techniques and Types

### 3.1.2 Function Testing

| | |
|---|---|
| **Technique Objective** | Exercise each function of the application, including data entry, processing and retrieval, and record its behaviour. Show that each function meets its use case and business rules, and that access is limited to authorised users. |
| **Technique** | Automated `pytest` tests drive the real FastAPI routers through a test client, exactly as the browser would, against a throwaway PostgreSQL 16 database started in Docker. Every test gets its own copy of the schema, so tests cannot affect each other. For each use case the tests supply valid data, invalid data and boundary data, and check the HTTP status, the message returned, and the rows and files actually stored. The tests cover:<br><br>**Valid data:** the expected result occurs.<br>**Invalid data:** the correct error status is returned (400, 401, 404, 413, 422, 503) and nothing is stored.<br>**Business rules:** each rule is checked with hand-calculated expected values.<br>**Authorisation:** every protected route is called anonymously and with forged, expired and malformed tokens, and users try to reach each other's data.<br>**Pure functions:** the small rule-bearing functions are tested directly with valid, boundary and garbage input.<br><br>Only two things are replaced with fakes: the YouTube lookup and the forecast model. The routers, validation, password hashing, token handling, file storage and database are real. |
| **Oracles** | The use cases and business rules in the project documentation; the documented response models; hand-calculated expected values (averages, category ranking, polling intervals, durations); the rows and files actually stored after each request. |
| **Required Tools** | Python and `pytest` 9.1; FastAPI `TestClient`; `psycopg2` 2.9; `PyJWT` (to forge and expire tokens); Pillow (to build test images); Docker 28 with the `pgvector/pgvector` image (throwaway database). |
| **Success Criteria** | Every valid case gives the expected result. Every invalid case is rejected with the right error and writes nothing. Every protected route rejects anonymous and forged callers. No user can read, change or delete another user's data. All tests pass, or each failure is recorded as a defect. |
| **Special Considerations** | The tests never read the live connection string: the test harness points the backend at the throwaway server and asserts that before any test runs. The real forecast model is not loaded (it is replaced by a fake that returns a fixed forecast), so these tests prove the application logic around the model, not the model's accuracy. Login and logout in a browser are not covered here (section 7). The tests are safe to repeat: the container is created and removed on every run. |

## 4. Test Results

Final full run of the whole backend suite, 20 September 2026: **358 passed, 1 skipped, 1 expected failure** (360 tests, 9 min 30 s). This includes the 109 function tests in `test_api_function.py`; the other 251 are the data and database integrity tests from the earlier report, which all still pass. No test containers were left running.

### 4.1 Function tests by area

| Area | Tests | What it proves |
|---|---:|---|
| Dashboard | 2 | The summary shows the signed-in user's own name, subscribers and monthly views, and edits to the profile appear in it |
| Trends | 7 | A user with no predictions gets zeros and empty lists (no error); drafts and completed predictions are counted separately; averages ignore drafts; the best category is the one with the highest average views; uncategorised predictions are never ranked; a prediction made through the API appears in the trends; users see only their own |
| Authorisation and tokens | 32 | All 14 protected routes reject anonymous callers (14) and forged tokens (14); expired, subject-less and `alg=none` tokens are rejected; a valid token works |
| Authentication and profile | 7 | Sign-up then log-in works and the token opens protected routes; profile updates ignore fields the user does not own (`email`, `id`, `password_hash`); an empty update changes nothing; a short new password is rejected; the channel page shows the stored snapshot; email case behaviour is recorded (defect F1) |
| Notifications | 2 | The unread count falls as notifications are read; notifications are listed newest first |
| Predictions | 16 | Dataset over 50 MB and thumbnail one byte over 10 MB are refused with 413 and store nothing; a thumbnail exactly at the limit is accepted; tags are split and trimmed and blanks dropped; the model receives tags, duration, description and category; six spellings of the draft flag are accepted; a draft needs no thumbnail but a completed run does; a missing title is 422; the list is newest first; delete then fetch is 404; deleting a user removes their predictions and notifications; a non-numeric id is 422 |
| Public data, forecast, uploads, CORS | 13 | `/health` reports a connected database; `/channels` returns correct engagement figures and survives channels with zero views or subscribers; videos and timeseries are returned for known ids and as an empty list (not an error) for unknown ids; timeseries is oldest first; `/forecast` returns the model result, is 503 when the model is not loaded, 400 for an unreachable thumbnail, 422 without a title; uploaded files are served, missing files are 404 and path-traversal attempts cannot read source files; the dev frontend origin is allowed and other origins are not |
| Business-rule functions | 30 | Schedule parsing and default upload time (1); channel URL parsing for `@handle`, `/channel/`, `/user/`, `/c/`, bare names, whitespace, and rejection of URLs with no channel (11); upload storage uses random names, keeps only the extension, and deleting is safe to repeat and cannot leave the uploads folder (2); polling interval steps at 1 h and 2 h, checked either side of each boundary (7); ISO-8601 duration parsing, including `PT8M32S`, `PT1H2M3S`, lower case, and NaN for `None`, empty and garbage input (9) |
| | **109** | |

### 4.2 Use cases and their coverage

Coverage combines the new tests with the 76 backend tests from the earlier report (`test_api_data.py`), which already covered the authentication, channel, notification and prediction functions.

| # | Use case | Valid case | Invalid and boundary cases | Result |
|---|---|---|---|---|
| F1 | Sign up | Account created, password stored as a bcrypt hash, welcome notification, subscribers copied from the channel | Duplicate email (400); invalid email, short password, missing field (422); failed channel lookup still creates the account and records the error; email case | Pass; F1 defect noted |
| F2 | Log in | Token and user returned | Wrong password and unknown email give the same 401 | Pass |
| F3 | Session token | Valid token opens protected routes | Missing, malformed, forged, expired, no-subject, non-numeric-subject, `alg=none` and deleted-user tokens are all 401 | Pass |
| F5 | Update profile | Only the given fields change | Negative values (422); protected fields ignored; empty body | Pass |
| F6 | Change password | Old password stops working | Wrong current password (400); short new password (422) | Pass |
| F7 | Channel view and refresh | Snapshot updated, error cleared | Failed refresh keeps the last good snapshot and writes an error notification | Pass |
| F8 | Notifications | Unread count correct, newest first | Another user's notification is 404 | Pass |
| F9 | Completed prediction | Model output stored, confidence within bounds, notification written, thumbnail saved | No thumbnail, wrong type, corrupt image, no linked channel (400); over-size files (413); model failures map to 422, 404, 503 and store nothing | Pass |
| F10 | Draft prediction | Saved without calling the model | Six spellings of the draft flag; no thumbnail needed | Pass |
| F11 | Schedule | Date and time stored as given, optional | Malformed date or time (422) | Pass |
| F12 | List, view, delete | Newest first; delete returns 204 | Another user's id and a deleted id are 404 | Pass |
| F13 | Dashboard summary | Own name, subscribers, monthly views | Anonymous is 401 | Pass |
| F14 | Trends summary | Correct totals, averages, best category, timeline, category breakdown | No predictions, drafts excluded, uncategorised excluded, missing confidence, other users' data excluded | Pass |
| F15 | Public data | Correct shapes and figures | Unknown ids give empty lists; zero denominators return 0 | Pass |
| F16 | Forecast endpoint | Model result returned | Model not loaded (503); bad thumbnail URL (400); missing title (422) | Pass |
| F17 | Uploaded files | File served | Missing file (404); path traversal blocked | Pass |
| F18 | Cross-origin rules | Dev frontend allowed | Other origins refused | Pass |

### 4.3 Authorisation matrix

Every protected route was called anonymously and with a forged token. All 28 calls returned 401, and no data was returned.

| Route group | Routes tested | Anonymous | Forged token |
|---|---:|---|---|
| Auth (`/auth/me` get and patch, `/auth/change-password`) | 3 | 401 | 401 |
| Channel | 2 | 401 | 401 |
| Notifications | 3 | 401 | 401 |
| Dashboard and trends | 2 | 401 | 401 |
| Predictions (list, create, get, delete) | 4 | 401 | 401 |

Access between users (user A against user B's notifications and predictions) was tested in the earlier backend tests and passes. The new dashboard and trends tests add the same check for those two functions.

## 5. Defects Found

| ID | Defect | How it was found | Severity | Resolution |
|---|---|---|---|---|
| F1 | Email addresses are case-sensitive. `Ann@Example.com` and `ann@example.com` can each sign up as separate accounts, and a user who types their email in a different case cannot log in. | Sign-up function test | Low | **Open.** Recorded in the test suite, which documents the current behaviour. Suggested fix: store and compare emails in lower case (or add a unique index on `lower(email)`), then change the test to expect one account. |

No high or medium severity defects were found by the function tests. The function tests also showed that the fixes from the earlier report (channel data kept on a failed refresh, no orphaned uploads, HTTP 422 for bad dates) still hold end to end.

During test development one test failed because the test expected the size tier to be `Micro`, while the application returns the label `Micro (<1K)`. This was an error in the test, not in the application, and was corrected.

## 6. Test Evidence

| Run | Date | Result | Time |
|---|---|---|---|
| Function tests, first run | 20 September 2026 | 99 passed, 1 failed (test expectation, corrected), 9 skipped (module-loading problem in the test, corrected) | 4 min 22 s |
| Function tests, corrected areas re-run | 20 September 2026 | 8 passed (size tier, polling); 9 passed (duration parsing) | 1 min 45 s; 2 min 41 s |
| **Whole backend suite (final)** | 20 September 2026 | **358 passed, 1 skipped, 1 expected failure** | 9 min 30 s |

The one skipped test compares a real database with the schema scripts and needs a live connection. The one expected failure is defect D10 from the database integrity report.

## 7. Limitations and Remaining Work

- **User interface, login and logout in a browser** are not covered. Per the test plan, these are tested manually and with a browser automation tool after function testing. The frontend routes to walk through are `/`, `/sign-up`, `/dashboard`, `/new-prediction`, `/prediction-result/:id`, `/trends`, `/settings` and `/channel`, plus the redirect of unknown paths to `/`.
- **The real forecast model** is replaced by a fake in these tests. The application logic around it is tested; its accuracy and its behaviour with real YouTube data are not.
- **Load and performance** behaviour is outside this report (the connection pool under concurrent use is covered in the database integrity report).

## 8. Conclusion

The application's functions were tested through the API in 109 new automated tests, together with the 76 backend data tests already in place. Every use case works with valid data, rejects invalid data with the right error without storing anything, applies its business rules with the expected values, and is closed to anonymous and forged callers. One low-severity defect (F1, email case) was found and is open with a suggested fix. The full backend suite of 360 tests passes (358 passed, 1 skipped, 1 expected failure). The remaining work is the manual and automated user interface testing described in section 7.

## Appendix – How to reproduce

From the `backend` folder, with Docker running:

```
pip install -r requirements-dev.txt
python -m pytest tests/test_api_function.py
```

Run `python -m pytest` for the whole suite. The function tests need Docker (or `TEST_DB_ADMIN_URL` pointing at a disposable server); see [DB_TESTING.md](DB_TESTING.md). The tests for ISO-8601 duration parsing and for the polling interval load the real `inference.py` and ETL job, so they are skipped automatically if those packages are not installed.
