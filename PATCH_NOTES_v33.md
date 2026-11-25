# DSS v33 — Official PDF Report

Based on DSS v32. This patch updates only `app/reports.py` for the PDF export; Excel export and existing API filters remain unchanged.

## PDF report changes
- Formal company heading: PT INDOFOOD FORTUNA MAKMUR.
- Report title: Laporan Resmi Rekapitulasi Suggestion.
- Period and department context, print date.
- Summary cards/table: total suggestions, Approved Final, in-process, rejected, and KPI points.
- Landscape A4 detailed table with repeating header row.
- Approval section with blank signature fields for Admin DSS and Manager; no names/signatures are fabricated.
- Footer with company/system name and page number.
- Download filename: DSS-Laporan-Resmi-PT-Indofood-Fortuna-Makmur.pdf.

## Installation
Replace `DSS-Backend/app/reports.py` with the file from this package. No database migration is required. Existing Excel endpoint is retained unchanged.

Note: Company logo is not included because no official logo asset was supplied. It can be added in a follow-up once provided.
