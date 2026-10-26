# Spike: monthly slips, A1/A2, and SPT 21/26

Scope: Step 1 spike only. No code changes. Frontend untouched.

## (a) TER source and monthly formula

Sources: PP 58/2023 (TER concept + rate tables in its appendices),
PMK 168/2023 (implementation: categories, monthly vs December math),
PER-02/PJ/2024 (form codes 1721-VII/VIII, A1, A2, SPT 21/26 layout).

TER categories (PMK 168/2023 Art. 3, by PTKP status):

| Category | PTKP statuses |
|----------|---------------|
| A | TK/0, TK/1, K/0 |
| B | TK/2, TK/3, K/1, K/2 |
| C | K/3 |

Rate lookup: monthly gross income bracket x category -> TER rate.
Authoritative numbers live ONLY in the PP 58/2023 appendix tables;
do not copy rates from blogs. Seed them as tariff data (like PTKP),
never hardcoded.

Monthly formula (PMK 168/2023 Art. 4, Jan-Nov and non-final months):

    monthly_tax = TER_rate(monthly_gross, ter_category) * monthly_gross

December / final-month correction (PMK 168/2023 Art. 5-6):

    annual_tax = art17_rate(annual_net - annual_ptkp)   # net after
        # 12x (gross - biaya_jabatan capped/mo - pension/iuran)
    december_tax = annual_tax - sum(monthly_tax Jan-Nov)

Rounding follows the PMK worked examples; the TER service must carry
a test vector per category plus one December annualization vector
taken verbatim from the PMK examples before build.

## (b) A1 vs A2 population

| Form | Population | Payer |
|------|-----------|-------|
| 1721-A1 | Permanent employees, pension recipients paid periodically (resident individuals) | Private / non-state payer |
| 1721-A2 | Civil servants, TNI/POLRI members, their pensioners | State payer via APBN/APBD |

Both are December / final-employment-month aggregates of the 12 (or
fewer) monthly slips: annual gross, annual withholding, identity,
and signing block. Read-only views; no separate create endpoint.

## (c) BPMP: new slip_type or BP21 subtype?

Monthly slip = one row per employee per month (gross, TER category,
computed withholding). It aggregates into A1/A2 and posts into SPT 21/26.

| Criterion | New `slip_type = bpmp` | BP21 subtype (flag) |
|-----------|------------------------|---------------------|
| Form identity | Distinct form (monthly slip vs one-off non-employee slip) | Shared 1721 family |
| Query/scope | Clean filter `slip_type == bpmp` reuses `resolve_document_scope` | Extra flag on every query |
| Validation | TER fields mandatory only on bpmp (schema split) | Conditional validation branches |
| A1/SPT aggregation | `where slip_type in (bpmp, bp26)` is explicit | Flag-aware aggregation everywhere |
| Migration | Pure enum addition + migration | Same migration plus flag column |

Decision: new `SlipType.bpmp`. Reuses the shared slips table,
lifecycle (draft/issued/invalid), and tenant scope; only the form
type and its TER validation differ. Existing `bp21` keeps its
non-employee meaning; no flag column.

## (d) SPT 21/26 minimal fields (reuse TaxReturn pattern)

New `TaxReturnType.pph_21_26`. Reuse header, lifecycle, and flow
unchanged: Konsep (create draft: type + period + kind) -> posting
(server-computed aggregates) -> pernyataan (signer name/NPWP/capacity)
-> Bayar dan Lapor (passphrase) -> billing when underpaid.
Same-row pembetulan via `correction_no`; same `resolve_document_scope`
tenant/class/siswa scoping as `tax_returns.py` and `schemas/tax_return.py`.

Minimal posted content:

- Header: `tax_month`, `tax_year`, totals (`total_income_tax`,
  `amount_due` reuse; PPN columns stay zero).
- Lines (`tax_return_lines`, `section` per 1721 enclosure):
  monthly-slip totals, self-paid, article-26 rows; `code` = KAP-KJS.
- Source scope: issued `bpmp` + `bp26` (+ `bp21` one-offs) of the period.

Open risk: December math test vectors must be checked against the PMK
numeric examples; wrong teaching is worse than a missing feature.
