# Decisions

## The decision lives in vendor-guard, not in n8n

The obvious n8n build is: email the approver a link to the Wait node's resume URL. Then anyone with that URL can approve, n8n cannot tell who clicked, and nothing stops the requester from forwarding it to themselves. So the approval links point to vendor-guard. It checks who the link belongs to, that it is not the requester, that the callback number matches, records the decision, and only then calls the resume URL. n8n moves messages, vendor-guard holds the rules. Moving the workflow to Power Automate would not touch the rules.

## Master data stays in stammdaten-check

vendor-guard has no vendor table. For a bank change it asks stammdaten-check for the record on file (bank details, phone, email). For a new vendor it asks for duplicates and whether the IBAN is already used elsewhere. One place for master data, the same way SAP would be the only source in a real setup.

If stammdaten-check is down, the request is not lost. After three tries the case opens with V01 "master data check not reachable" and goes to a person.

## Callback to the number on file

Payment diversion letters usually come with a phone number for "questions". Calling that number reaches the fraudster. The approval page shows the number from the master record in large type, and the approver has to type the number they called. The last seven digits must match the number on file. It does not prove the call happened, but it makes skipping it a conscious lie instead of a click.

## Two approvers for high risk

Unknown sender domain (V06), a change within two days of the payment run (V08) and a sanctions hit (V10) need two different approvers. Everything else needs one. Two approvers for every bank change would sound safer, but in a company with a few hundred bank changes a year it turns into rubber stamping, and rubber stamping is worse than one careful look.

## A sanctions hit never blocks on its own

The list is matched fuzzy, because listed companies show up with spelling variants. Fuzzy matching on company names produces false hits, especially for short or common names. A block based on a false hit would stop a legitimate supplier with no explanation. A hit sends the case to two people and says compliance has to clear it.

## Rules instead of a language model for the PDFs

IBANs, VAT IDs and register numbers have strict formats and check digits. Regular expressions plus the checksum find them reliably, run in milliseconds and give the same answer every time. A model would add cost and variance for fields that do not need it. If supplier documents were scans, OCR would be the next step, not a model.

## Signed audit chain, and its limit

Every audit entry carries the HMAC of the previous entry plus its own content. Changing an old entry breaks the chain, and `/api/v1/audit/verify` shows where. Someone with the database and the key can rebuild the chain. That is why the key is an environment variable outside the database, and why the newest hash is emailed every evening. An old email with a hash that no longer matches the log is proof the log was rewritten.

## Idempotency

n8n retries failed HTTP calls, and webhooks get delivered twice now and then. Without protection, one supplier form would open two cases and send two sets of approval mails. Every intake carries an Idempotency-Key (from the caller, or the n8n execution id). The same key returns the existing case with HTTP 200 instead of 201.

## Payment run calendar

The payment run days are configuration (Tuesday and Friday by default, `PAYMENT_RUN_WEEKDAYS`). Reading the real F110 schedule from SAP would be better. For a project without SAP access, a calendar rule shows the idea.

## What I tested and how

The unit tests use a fake stammdaten-check so they run without a server. On top of that I ran both services for real, generated the four demo requests from the stammdaten-check sample data and pushed them through the API and the approval pages in a browser. The duplicate demo request found two existing vendors, one of them a duplicate that the stammdaten-check generator had planted with a typo ("Lachamnn"). I had not set that up on purpose.

Then the same requests went through n8n 2.45.4, with all emails caught by a local mail sink:

1. Fraud letter: n8n answered 202 at once, each approver got one email. A rejection with a comment resumed the waiting workflow, and the requester got the reason.
2. Real bank change: two approvals with the callback number, the export CSV was written, the requester got the approved email.
3. Clean new vendor: approved without a person, created email sent.
4. Broken input: the caller got a clean 422. vendor-guard stopped: 502 after the three retries, about four seconds.
5. Nobody decides: tested with a copy of the workflow where the wait is one minute instead of 48 hours. The escalation email went out.
6. Error workflow: it only fired after I published it. Easy to miss in n8n 2.x.
7. Audit anchor: tested with a webhook trigger in place of the schedule. The email showed the chain head and "intact".
