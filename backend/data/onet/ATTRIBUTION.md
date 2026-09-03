# O*NET 31.0 — attribution

The interview question bank is derived from the **O*NET 31.0 Database**, published by the
U.S. Department of Labor, Employment and Training Administration (USDOL/ETA), and used
under the **Creative Commons Attribution 4.0 International License**
(https://creativecommons.org/licenses/by/4.0/).

O*NET® is a trademark of USDOL/ETA. HireIQ has modified the data: task statements are
mapped to our skill taxonomy and rewritten as interview probes at five difficulty bands.
USDOL/ETA has not reviewed or approved these modifications.

Source file: `task_statements.csv` from
https://www.onetcenter.org/dl_files/database/db_31_0_csv/task_statements.csv

Every seeded question stores `source = "onet:<Task ID>"`, so any question a candidate is
asked traces back to a public occupational record rather than to our own guesswork.

## Why this source

For a product that scores real people, provenance is not a detail. The interview-question
dumps on Kaggle and HuggingFace are anonymous scrapes under unstated or "other" licences.
O*NET is a government occupational database with an explicit licence and a stable id per
task — and it answers the harder question: *which areas is it legitimate to probe for
this role*, rather than just supplying a list of questions someone once asked.
