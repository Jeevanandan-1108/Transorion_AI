# Overall test result

- source: `FILES/IMPORT` (20 PDFs)
- templates: SEZ import Template, SEZ export Template, Focus import Template, Focus export Template
- runs: 80 in 644.1s

## Per template

| Template | Runs | Validation passed | Crashed | Item rows | Rows with NO part number | Cost (Rs) |
|---|---|---|---|---|---|---|
| SEZ import Template | 20 | 19 | 0 | 94 | 2 | 4.42 |
| SEZ export Template | 20 | 19 | 0 | 94 | 1 | 4.5 |
| Focus import Template | 20 | 19 | 0 | 94 | 2 | 3.8 |
| Focus export Template | 20 | 19 | 0 | 94 | 2 | 4.55 |

## Validation errors by rule

| Rule | Count |
|---|---|
| PART_NUMBERS_REQUIRED | 7 |

Files: `_summary.csv` (one row per pdf x template), `_part_numbers.csv` (one row per line item), `_validation_issues.csv`.
