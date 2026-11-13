# Overall test result

- source: `FILES/IMPORT` (20 PDFs)
- templates: SEZ import Template, SEZ export Template, Focus import Template, Focus export Template
- runs: 80 in 695.3s

## Per template

| Template | Runs | Validation passed | Crashed | Item rows | Rows with NO part number | Cost (Rs) |
|---|---|---|---|---|---|---|
| SEZ import Template | 20 | 19 | 0 | 94 | 1 | 4.27 |
| SEZ export Template | 20 | 19 | 0 | 94 | 1 | 4.14 |
| Focus import Template | 20 | 19 | 0 | 94 | 1 | 3.92 |
| Focus export Template | 20 | 19 | 0 | 94 | 1 | 4.56 |

## Validation errors by rule

| Rule | Count |
|---|---|
| PART_NUMBERS_REQUIRED | 4 |

Files: `_summary.csv` (one row per pdf x template), `_part_numbers.csv` (one row per line item), `_validation_issues.csv`.
