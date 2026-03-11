# O365 Live Test Against CBarker Account

**Date:** 2026-03-10
**Task:** 59 — Test O365 monitor against live CBarker account

## Test Account
- Email: CBarker@allanedwards.com
- Tenant ID: 055e5bde-1fda-4a76-bd72-576a69a48d72 (allanedwards.com)
- No 2FA confirmed

## Results Summary

| Test | Result | Notes |
|------|--------|-------|
| ROPC auth (Azure CLI client) | FAIL | AADSTS65002 — consent not configured for first-party app |
| ROPC auth (MS Office client) | PASS | Client ID `d3590ed6-52b3-4102-aeff-aad2292ab01c` works |
| ROPC auth (MS Teams client) | PASS | Also works, has Mail.ReadWrite scope |
| ROPC auth (PowerShell client) | PASS | Works but lacks Mail scopes |
| List inbox messages | PASS | 0 unread, 0 total in inbox |
| Create draft | PASS | Successfully created and deleted test draft |
| Delete draft | PASS | HTTP 204 |
| CLI `monitor --once` | PASS | Full pipeline runs, 0 messages processed |
| List mail folders | PASS | All folders accessible |

## Key Finding: Client ID Must Change

The default client ID in `outlook.py` (`04b07795-8ddb-461a-bbee-02f9e1bf7b46`, Azure CLI) does **not** work with this tenant. The error is:

> AADSTS65002: Consent between first party application and first party resource must be configured via preauthorization

**Fix:** Use the Microsoft Office client ID `d3590ed6-52b3-4102-aeff-aad2292ab01c` instead. This client has pre-authorized Graph API access including `Mail.ReadWrite` and `Mail.Send` scopes.

The scope must be `https://graph.microsoft.com/.default` (not individual scopes like `Mail.Read`) when using this client ID.

## Required .env Variables

```
O365_EMAIL=<your-mailbox@yourdomain.com>
O365_PASSWORD=<your-password>
O365_CLIENT_ID=d3590ed6-52b3-4102-aeff-aad2292ab01c
O365_SCOPES=https://graph.microsoft.com/.default
```

## Scopes Granted (MS Office Client)

The token includes these Graph scopes:
- Mail.ReadWrite, Mail.Send (needed for inbox read + draft creation)
- Calendar.ReadWrite, Contacts.ReadWrite
- Files.Read, Files.ReadWrite.All
- User.Read.All, Directory.Read.All
- And many others (full O365 suite)

## Inbox State

The CBarker mailbox is empty:
- Inbox: 0 messages
- Drafts: 0 messages
- Sent Items: 1 message
- Deleted Items: 4 messages (1 unread)

No RFQ emails available to test classifier or full quote pipeline.

## What Was NOT Tested (Blocked)

1. **RFQ classifier** — no emails in inbox to classify
2. **Full RFQ→Quote→Draft pipeline** — needs an RFQ email in inbox
3. **Processed folder move** — needs messages to process
4. **Draft with PDF attachment** — needs RFQ to generate quote

## Recommendations

1. **Update default client ID** in `outlook.py` from Azure CLI to MS Office (`d3590ed6-52b3-4102-aeff-aad2292ab01c`), or document that `O365_CLIENT_ID` env var must be set.
2. **Send a test RFQ email** to CBarker@allanedwards.com to test the full pipeline (classifier → parser → pricing → PDF → draft).
3. **Consider registering a custom Azure AD app** for production use instead of relying on first-party client IDs, which may change permissions without notice.

## Code References

- Auth flow: `src/allenedwards/outlook.py:78-93` (`_acquire_token`)
- Inbox read: `src/allenedwards/outlook.py:121-147` (`list_unread_messages`)
- Draft creation: `src/allenedwards/outlook.py:157-191` (`create_draft`)
- Monitor pipeline: `src/allenedwards/monitor.py:98-115` (`run_once`)
- CLI entry: `src/allenedwards/cli.py:480-533` (`monitor` command)
