# Controls

What can go wrong when vendors are created or bank details change, and which control in this project answers it. Written the way an internal control system (IKS) or an auditor would list it.

| Risk | Control | Rule | Evidence |
|---|---|---|---|
| Payment goes to a fraudster after a fake "new bank details" letter | Every bank change needs a second person. The approver must call the vendor on the number already on file and enter that number. | V04 | approval record with callback number, audit entry |
| Letter comes from a look-alike domain | Sender domain compared with the vendor's email domain on file | V06 | risk on the case, two approvers needed |
| Change arrives two days before the payment run, so nobody checks | Distance to the next payment run computed from the run calendar | V08 | risk on the case, two approvers needed |
| Account belongs to someone else | Account holder compared with vendor name. The bank checks the name again at payment (Verification of Payee, mandatory in the EU since October 2025). | V07 | risk on the case |
| Fraudster's account already used for another "vendor" | IBAN looked up across all vendors and customers in the master data | V03 | risk with the other partner numbers |
| Letter and form disagree, someone typed a different IBAN | IBAN and VAT ID read from the PDF and compared with the form | V09 | extracted fields stored with the case |
| Pressure to skip checks ("dringend", "vertraulich") | Pressure words in the letter flagged | V12 | risk on the case |
| Business with a sanctioned company | Name and account holder screened against the EU consolidated list. A hit sends the case to a person, it never blocks automatically. | V10 | match with EU reference number |
| Same company created twice | Duplicate search in the master data before onboarding | V02 | duplicate candidates with score |
| Broken or invalid data enters the master | Field checks: IBAN check digits, VAT ID format and check digit, postal code, required fields | V01, V11 | risk on the case, case rejected on blockers |
| Requester approves their own request | Requester never gets an approval link, and the server checks again on submit | | link list per case |
| Approval link forwarded or reused | Links are signed, bound to one approver, valid 48 hours, usable once | | links table with used timestamp |
| Someone edits the history afterwards | Audit log as a signed hash chain, verify on demand, latest hash emailed daily | | `/api/v1/audit/verify`, daily email |
| Webhook delivered twice, vendor created twice | Idempotency key per request | | idempotency table |
| A system is down and the request gets lost | Retries with backoff, an error workflow that emails the admin, and a fallback case when stammdaten-check is unreachable (V01 tells the approver to check by hand) | | n8n error workflow, risk on case |

## What this does not cover

- Who the approver is. The link proves access to a mailbox, not a person. A company rollout should put the approval page behind the company login (Entra ID or similar).
- The bank account itself. Whether the IBAN exists and belongs to the vendor is only confirmed by the bank, at payment, through Verification of Payee.
- Changes made directly in SAP. If someone edits LFBK in transaction XK02 or BP, this tool never sees it. In SAP that gap is closed with dual control on sensitive fields (transaction FK08/FK09 for vendors, or the BP equivalent in S/4HANA).
